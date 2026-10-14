"""`djlib identify LINK`: every song in a YouTube or SoundCloud set, with how sure djlib is.

The background service does the listening (an identify job); this command shows how far it
got and then the tracklist. `djlib set LINK` uses the same identification when a set's own
tracklist is missing or has IDs.
"""

import codecs
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.text import Text

from djlib.domain.errors import AppError
from djlib.interfaces import terminal
from djlib.interfaces.envelope import envelope
from djlib.interfaces.terminal import plural

PHASES = {
    "queued": "Starting",
    "reading_page": "Reading the set's page and comments",
    "downloading": "Downloading the set's audio",
    "listening": "Listening to the set",
    "checking_gaps": "Listening again where songs change",
    "merging": "Putting the tracklist together",
}
EVENT_SECONDS = 15
POLL_SECONDS = 0.5
TRACKLIST_BYTES = 1_000_000
# Lines djlib skipped are quoted from the page or file: cut short, they stay data.
QUOTED_CHARS = 160


class Recognizer(StrEnum):
    """Song recognition services: Shazam (unofficial), or licensed AudD and ACRCloud."""

    shazam = "shazam"
    audd = "audd"
    acrcloud = "acrcloud"


# Why djlib can't listen on this computer at all; `djlib set` then uses the published list.
UNAVAILABLE = frozenset({"DEPENDENCY_REQUIRED", "RECOGNIZER_NOT_CONFIGURED"})


def tracklist_source(path: Path, text: str) -> str:
    """Where a tracklist file came from, as its evidence says: 1001Tracklists, MixesDB or a
    plain tracklist file."""
    hay = f"{path.name}\n{text[:5000]}".casefold()
    if "1001tracklists" in hay or "1001 tracklists" in hay or path.stem.startswith("1001"):
        return "1001tracklists"
    if "mixesdb" in hay:
        return "mixesdb"
    return "file"


CANT_READ = "Can't read that tracklist"
# TextEdit saves rich text by default; its control words would read as tracklist lines.
RICH_TEXT = b"{\\rtf"
# "soundcloud.com/…" typed without https://.
BARE_LINK = re.compile(
    r"^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|soundcloud\.com|on\.soundcloud\.com)/", re.I
)
# Words that stand for text instead of a file: what was copied, or what is piped in.
PASTE, STDIN = "paste", "-"
# A link inside copied words ("check this set https://youtu.be/…!").
LINK = re.compile(r"https?://[^\s<>\"'‘’“”]+")
# cmd.exe passes quotes through, and Notes or Messages curl them: 'LINK', “LINK”, ‘LINK’.
QUOTES = {"'": "'", '"': '"', "‘": "’", "’": "’", "“": "”", "”": "”"}


def unreadable(message: str) -> AppError:
    return AppError("INPUT_INVALID", message, details={"title": CANT_READ})


def unquoted(value: str) -> str:
    """The argument without one pair of quotes around it, unless a file has that very name."""
    text = value.strip()
    if len(text) > 2 and QUOTES.get(text[0]) == text[-1] and not Path(value).exists():
        return text[1:-1].strip()
    return value


def from_rich_text(data: bytes, name: str) -> bytes:
    """Plain text from rich text, by macOS's own converter."""
    if sys.platform == "darwin":
        command = ["textutil", "-convert", "txt", "-format", "rtf", "-encoding", "UTF-8"]
        try:
            done = subprocess.run(
                [*command, "-stdin", "-stdout"],
                input=data,
                capture_output=True,
                timeout=10,
                check=True,
            )
            return done.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    raise unreadable(
        f"{name} is a rich-text file (TextEdit's default). In TextEdit choose Format → Make "
        "Plain Text, save, and run this again — or copy the tracklist and run djlib set paste."
    )


def plain_text(data: bytes, name: str) -> str:
    """A tracklist's text in the encodings editors save: UTF-8 (with or without a BOM),
    UTF-16 with a BOM, else Windows' cp1252; rich text goes through macOS's converter."""
    if data.lstrip().startswith(RICH_TEXT):
        data = from_rich_text(data, name)
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        with contextlib.suppress(UnicodeDecodeError):
            return data.decode("utf-16")
    elif b"\x00" not in data:  # a zero byte means a document or audio file, not text
        for encoding in ("utf-8-sig", "cp1252"):
            with contextlib.suppress(UnicodeDecodeError):
                return data.decode(encoding)
    raise unreadable(
        f"djlib can't read {name} as text. Save it as plain text "
        "(TextEdit: Format → Make Plain Text)."
    )


def bare_link(given: str, command: str = "set") -> AppError | None:
    """ "soundcloud.com/…" typed without https://: the whole link back, quoted for zsh."""
    if not BARE_LINK.match(given):
        return None
    link = f"https://{given}"
    quoted = f"'{link}'" if "'" not in link else shlex.quote(link)
    return AppError(
        "INPUT_INVALID",
        f"{given} looks like a set link without https:// in front.",
        details={
            "title": "That link needs its https://",
            "next": f"djlib {command} {quoted}",
            "next_label": "The whole link, in single quotes",
        },
    )


def not_found(path: Path, instead: str, given: str | None = None) -> AppError:
    """Why a named tracklist file isn't there, and the way forward. ``given`` is the
    argument as typed (a Path would turn https:// into https:/)."""
    given = given or str(path)
    if (error := bare_link(given)) is not None:
        return error
    where = "" if path.is_absolute() else f" in {terminal.short_path(str(Path.cwd()))}"
    return unreadable(
        f"Can't find {given}{where}. Drag the file into this window to paste its full path, "
        f"or copy the tracklist and run {instead}."
    )


def read_text_file(
    path: Path,
    limit: int = TRACKLIST_BYTES,
    instead: str = "djlib set paste",
    given: str | None = None,
) -> str:
    """A tracklist file's text: a regular file of at most ``limit`` bytes. ``instead`` is the
    command that takes a copied tracklist where the file can't be found; ``given`` is the
    file as the DJ typed it."""
    try:
        if path.is_dir():
            raise unreadable(f"{path} is a folder; give the tracklist file inside it.")
        if not path.exists():
            raise not_found(path, instead, given)
        if not path.is_file():
            raise unreadable(f"{path} is not a text file.")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        reason = (exc.strerror or "it can't be read").lower()
        raise unreadable(f"djlib can't open {path}: {reason}.") from exc
    if len(data) > limit:
        raise unreadable(f"{path} is over {limit // 1_000_000} MB; a tracklist is smaller.")
    text = plain_text(data, path.name)
    if not text.strip():
        raise unreadable(
            f"{path.name} is empty; paste the set's tracklist into it, or copy it and run "
            f"{instead}."
        )
    return text


def typed(value: str | Path) -> str | None:
    """``paste`` (what was copied) or ``-`` (what is piped in), else None. A file named paste
    in this folder is still that file."""
    text = unquoted(str(value)).strip()
    if text == STDIN:
        return STDIN
    if text.lower() == PASTE and not Path(text).exists():
        return PASTE
    return None


def clipboard() -> bytes:
    """What was last copied, through macOS's pbpaste."""
    if sys.platform != "darwin" or shutil.which("pbpaste") is None:
        raise AppError(
            "INPUT_INVALID",
            "djlib set paste reads the clipboard on a Mac; here pipe it in: … | djlib set -",
        )
    try:
        done = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            timeout=10,
            check=True,
            env={**os.environ, "LANG": "en_US.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "INPUT_INVALID", "djlib couldn't read the clipboard. Copy it again, then retry."
        ) from exc
    return done.stdout


def links_in(text: str) -> list[str]:
    """The http(s) links in copied text, each once, without the punctuation after them."""
    return list(dict.fromkeys(found.rstrip(".,;:!?)]}>") for found in LINK.findall(text)))


def songs_in(text: str) -> list:
    """The songs a copied text names: its “Artist - Title” lines, links aside."""
    from djlib.application.tracklists import parse_tracklist

    items, _ = parse_tracklist(text)
    return [item for item in items if not LINK.search(f"{item.artist or ''} {item.title or ''}")]


def read_typed(kind: str, limit: int = TRACKLIST_BYTES) -> tuple[str | None, str]:
    """(link, text) for ``paste`` or ``-``: a single http(s) link is used as if typed (link
    set), as is the one link in words that name no song; anything else is tracklist text
    with a file's limits. The caller that uses the link says so (``say_link``)."""
    from djlib.interfaces.set_sources import is_url

    if kind == STDIN:
        stream = getattr(sys.stdin, "buffer", None)
        term = terminal.current()
        if stream is not None and sys.stdin.isatty() and not term.json:
            # Nothing is piped in: the DJ types or pastes it here.
            term.err.print(
                "Paste the tracklist, then press Ctrl-D on an empty line.", style="muted"
            )
        data = stream.read(limit + 1) if stream is not None else b""
        where = "The text piped in"
    else:
        data, where = clipboard(), "The clipboard"
    if len(data) > limit:
        raise unreadable(f"{where} is over {limit // 1_000_000} MB; a tracklist is smaller.")
    text = plain_text(data, where.lower())
    if not text.strip():
        if kind == STDIN:
            raise AppError("INPUT_INVALID", "Nothing was piped in: … | djlib set -")
        raise AppError(
            "INPUT_INVALID",
            "Copy the set's link or its tracklist, then run this again.",
            details={"title": "The clipboard is empty"},
        )
    word = text.strip()
    if is_url(word) and not any(char.isspace() for char in word):
        return word, text
    links = links_in(text)
    if len(links) == 1 and not songs_in(text):
        return links[0], text
    return None, text


def say_link(kind: str, link: str) -> None:
    """Show which link djlib took from what was copied."""
    term = terminal.current()
    if kind == PASTE and not term.json:
        term.err.print(Text(f"Using the link you copied: {link}", style="muted"), soft_wrap=True)


def tracklist_wanted(kind: str) -> AppError:
    """--tracklist paste (or --text paste) while the set's link is still what was copied."""
    if kind == STDIN:
        return AppError(
            "INPUT_INVALID",
            "The text piped in is a link; pipe in the set's tracklist instead.",
            details={"title": "That's the set's link"},
        )
    return AppError(
        "INPUT_INVALID",
        "The clipboard still holds the set's link. Copy the tracklist on the page, then run "
        "the same command again.",
        details={"title": "Copy the tracklist too"},
    )


def nothing_in(kind: str, text: str, command: str = "set") -> AppError:
    """What was copied (or piped in) holds neither a usable link nor a tracklist."""
    place = "the clipboard" if kind == PASTE else "the text piped in"
    if command == "identify" and songs_in(text):
        return AppError(
            "INPUT_INVALID",
            f"{place[:1].upper()}{place[1:]} holds a tracklist, not a set link.",
            details=ways(
                [
                    ("Check it against your music", f"djlib set {kind}"),
                    (
                        "Or listen to the set too, with its link in place of LINK",
                        f"djlib identify 'LINK' --tracklist {kind}",
                    ),
                ],
                "That's a tracklist",
            ),
        )
    if len(links := links_in(text)) > 1:
        return AppError(
            "INPUT_INVALID",
            f"There are {len(links)} links in {place}; djlib checks one set at a time. Copy "
            "just the set's link, then run this again.",
            details={"title": "Copy just the set's link"},
        )
    if command == "identify":
        return AppError(
            "INPUT_INVALID",
            f"There's no set link in {place}. Copy the set's YouTube or SoundCloud link, then "
            "run this again.",
            details={"title": f"No set link in {place}"},
        )
    message = (
        f"There's no set link and no “Artist - Title” line in {place}. Copy the set's link or "
        "its tracklist, then run this again."
    )
    if "\t" in text:
        message += " From a spreadsheet? Copy its Artist and Title header row too."
    return AppError("INPUT_INVALID", message, details={"title": f"No set or tracklist in {place}"})


def pasted_name(text: str) -> str:
    """ "Pasted 13 Oct · Lumen - Halo + 11 more": a copied tracklist without a heading, named
    by the day, its first song and how many follow. Another paste gets its own name, so
    rekordbox never takes it for a newer version of a different set (DJs reuse openers); the
    same paste keeps its name and its list."""
    today = date.today()
    name = f"Pasted {today.day} {today:%b}"
    songs = [item for item in songs_in(text) if item.kind == "named"]
    if not songs:
        return name
    song = " ".join(f"{songs[0].artist} - {songs[0].title}".split())
    name += f" · {song if len(song) <= 48 else song[:47] + '…'}"
    return name + (f" + {len(songs) - 1} more" if len(songs) > 1 else "")


def nothing_given(command: str) -> AppError:
    """`djlib set` or `djlib identify` with nothing to check: the ways in."""
    link = "'https://soundcloud.com/…'"
    if command == "identify":
        steps = [
            ("A set link (in single quotes)", f"djlib identify {link}"),
            ("A link you copied", "djlib identify paste"),
        ]
        title = "Which set should djlib identify?"
        message = "Give it the link of a YouTube or SoundCloud set."
    else:
        since = (date.today() - timedelta(days=30)).isoformat()
        steps = [
            ("A set link (in single quotes)", f"djlib set {link}"),
            ("A tracklist or link you copied", "djlib set paste"),
            ("Your recent Shazams", f"djlib set shazam:{since}"),
        ]
        title = "What should djlib check?"
        message = (
            "Give it a set link, a tracklist or your Shazams. For a tracklist file: type "
            "djlib set, a space, then drag the file in."
        )
    return AppError("INPUT_INVALID", message, details=ways(steps, title))


def ways(steps: list[tuple[str, str]], title: str | None = None) -> dict:
    """An error's details with more than one way forward: ``next`` stays the first command,
    and ``next_steps`` lists them all (a terminal shows each on its own line)."""
    label, command = steps[0]
    details = {"next": command, "next_label": label}
    details["next_steps"] = [{"label": label, "command": command} for label, command in steps]
    return {"title": title, **details} if title else details


def read_tracklists(paths: list[Path] | None) -> list[dict]:
    found = []
    for path in paths or []:
        if kind := typed(path):
            link, text = read_typed(kind)
            if link is not None:
                raise tracklist_wanted(kind)
            found.append({"source": tracklist_source(Path(kind), text), "text": text})
            continue
        given = unquoted(str(path))
        path = Path(given).expanduser()
        text = read_text_file(path, instead="the same command with --tracklist paste", given=given)
        found.append({"source": tracklist_source(path, text), "text": text})
    return found


def submit(
    local,
    url: str,
    *,
    tracklists: list[dict] | None = None,
    again: bool = False,
    recognizer: str | None = None,
    name: str | None = None,
) -> dict:
    """Queue an identification. A fresh retry token each time: a set identified before is
    reused by the service, and a failed one resumes from what it heard."""
    body = {
        "url": url,
        "tracklists": tracklists or [],
        "again": again,
        "idempotency_key": f"identify-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}",
    }
    if recognizer:
        body["recognizer"] = recognizer
    if name:
        body["name"] = name
    return local.request("POST", "/identifications", data=body)["result"]


def _minutes(seconds) -> str:
    if not isinstance(seconds, int | float):
        return ""
    if seconds < 90:
        return "under 2 min left" if seconds >= 45 else "less than a minute left"
    return f"about {round(seconds / 60)} min left"


def waiting_text(waiting: dict) -> str:
    """ "Shazam asked djlib to slow down; trying again in 20 s (gives up after 14 min)"."""
    service = waiting.get("service") or "Song recognition"
    seconds = max(int(waiting.get("retry_in") or 0), 1)
    if waiting.get("reason") == "rate_limited":
        text = f"{service} asked djlib to slow down; trying again in {seconds} s"
        if isinstance(left := waiting.get("gives_up_in"), int | float):
            text += f" (gives up after {max(round(left / 60), 1)} min more)"
        return text
    return f"{service} didn't answer; trying again in {seconds} s"


def progress_line(job: dict) -> str:
    """ "Listening to the set… 34 of 124 windows · 6 songs so far · about 2 min left"."""
    progress = (job.get("result") or {}).get("progress") or {}
    phase = progress.get("phase") if job.get("state") == "running" else job.get("state")
    text = PHASES.get(phase or "", "Identifying the set") + "…"
    if phase == "queued" and progress.get("ahead"):
        text = "Waiting for the set before it to finish…"
    parts = []
    if phase in {"listening", "checking_gaps"} and progress.get("windows_total"):
        parts.append(f"{progress.get('windows_done') or 0} of {progress['windows_total']} windows")
    if phase == "downloading" and progress.get("downloaded_bytes"):
        parts.append(f"{progress['downloaded_bytes'] / 1_000_000:.0f} MB")
    if phase in {"listening", "checking_gaps", "merging"} and progress.get("songs_heard"):
        parts.append(f"{plural(int(progress['songs_heard']), 'song')} so far")
    if phase in {"listening", "checking_gaps"} and isinstance(progress.get("waiting"), dict):
        parts.append(waiting_text(progress["waiting"]))
    elif phase in {"listening", "checking_gaps"} and (eta := _minutes(progress.get("eta_seconds"))):
        parts.append(eta)
    return f"{text} {' · '.join(parts)}" if parts else text


def follow(local, job: dict, url: str | None = None) -> dict:
    """Wait for an identification, showing progress; Ctrl-C cancels it.

    A terminal gets a live status line; scripts and assistants get a JSON event on stderr
    when the job starts, at each phase and at most every 15 seconds in between.
    """
    from djlib.interfaces.rekordbox_cli import event

    term = terminal.current()
    job_id = job["job_id"]
    live = None
    status = None
    if term.json:
        event("identify_started", job_id=job_id, url=url)
    else:
        status = term.err.status(f"[bold]{progress_line(job)}", spinner_style="accent")
        live = status.__enter__()
    last_phase, last_event = None, 0.0
    try:
        while True:
            progress = (job.get("result") or {}).get("progress") or {}
            phase = progress.get("phase") if job.get("state") == "running" else job.get("state")
            if live is not None:
                live.update(f"[bold]{progress_line(job)}")
            elif phase != last_phase or time.monotonic() - last_event >= EVENT_SECONDS:
                event(
                    "identify_progress",
                    job_id=job_id,
                    **{
                        "phase": phase,
                        **{key: progress.get(key) for key in progress if key != "phase"},
                    },
                )
                last_event = time.monotonic()
            last_phase = phase
            if job.get("state") not in {"queued", "running"}:
                return job
            time.sleep(POLL_SECONDS)
            job = local.request("GET", f"/jobs/{job_id}")["result"]
    except KeyboardInterrupt:
        # It may have finished meanwhile; what it heard is saved either way.
        with contextlib.suppress(AppError):
            local.request("POST", f"/jobs/{job_id}/control", data={"action": "cancel"})
        if status is not None:
            status.__exit__(None, None, None)
            status = None
        message = (
            "Stopped listening. What djlib heard so far is saved: run the same command to continue."
        )
        if term.json:
            error = AppError("JOB_CANCELLED", message, details={"job_id": job_id})
            term.print_json(envelope(error=error.as_dict()))
        else:
            term.err.print(message)
        raise typer.Exit(130) from None
    finally:
        if status is not None:
            status.__exit__(None, None, None)


def outcome(job: dict) -> dict:
    """The finished identification's result, or the reason it stopped as an AppError."""
    result = job.get("result") or {}
    if job.get("state") == "completed":
        return result
    error = result.get("error") or {}
    if job.get("state") == "failed" and error:
        raise AppError(
            error.get("code") or "JOB_FAILED",
            error.get("message") or "The identification failed.",
            retryable=bool(error.get("retryable")),
            details={**(error.get("details") or {}), "job_id": job.get("job_id")},
        )
    if job.get("state") == "paused":
        raise AppError(
            "JOB_PAUSED",
            f"The identification is paused; continue it with djlib jobs control "
            f"{job.get('job_id')} resume.",
        )
    raise AppError(
        "JOB_CANCELLED",
        "The identification was cancelled. What djlib heard is saved: run it again to continue.",
    )


def warnings_of(result: dict) -> list[str]:
    """Notices first, then the tracklist lines djlib could not read."""
    from djlib.application.set_identification import SOURCE_NAMES

    lines = []
    for line in result.get("skipped_lines") or []:
        where = SOURCE_NAMES.get(line["source"], line["source"])
        lines.append(
            f"{where[:1].upper()}{where[1:]}, line {line['line']} skipped ({line['reason']}): "
            f"“{quoted(line['text'])}”"
        )
    return [*(result.get("notices") or []), *lines]


def quoted(text: object) -> str:
    """Publisher text quoted in djlib's own messages: one line, cut short."""
    from djlib.domain.contracts import safe_text

    flat = " ".join(safe_text(text).split())
    return flat if len(flat) <= QUOTED_CHARS else flat[: QUOTED_CHARS - 1] + "…"


def cannot_listen(error: AppError) -> str:
    """Why djlib can't listen on this computer (no final period); ``listen_fix`` has the
    command that fixes it."""
    if error.code == "DEPENDENCY_REQUIRED" and "FFmpeg" in error.message:
        return "FFmpeg isn't installed"
    if error.code == "DEPENDENCY_REQUIRED" and "yt-dlp" in error.message:
        return "yt-dlp isn't installed"
    if error.code == "DEPENDENCY_REQUIRED":
        return "song recognition isn't installed"
    return error.message.rstrip(".")


def listen_fix(error: AppError) -> str | None:
    """The command that lets djlib listen to sets here, or None."""
    from djlib.application.identification import INSTALLER
    from djlib.interfaces.terminal import ffmpeg_install

    if error.code != "DEPENDENCY_REQUIRED":
        return "djlib service stop" if error.code == "RECOGNIZER_NOT_CONFIGURED" else None
    if "FFmpeg" in error.message:
        return ffmpeg_install() or "djlib doctor"
    return INSTALLER


def wanted(choice: bool | None, text: str, url: str, workspace, *, complete: bool = True) -> bool:
    """Whether `djlib set LINK` listens to the set: when asked to, or by default when its
    published tracklist is missing, has IDs or is not ``complete`` (as MixesDB marks some),
    or it was identified before (that costs nothing)."""
    from djlib.application.identification import finished
    from djlib.application.tracklists import parse_tracklist

    if choice is not None:
        return choice
    if not text.strip() or not complete:
        return True
    items, _ = parse_tracklist(text, url)
    if any(item.kind == "unknown" for item in items):
        return True
    return finished(workspace, url) is not None


def listen(local, url: str, tracklists: list[dict] | None = None, name: str | None = None):
    """Identify a set and wait for it: ``{"job_id", "result"}`` (raises why it stopped)."""
    job = follow(local, submit(local, url, tracklists=tracklists, name=name), url)
    return {"job_id": job["job_id"], "result": outcome(job)}


def identified_request(result: dict, name: str):
    """The request list for a set from its identified tracklist, in set order; IDs stay
    IDs, with their likely songs in the notes."""
    import hashlib
    import json

    from djlib.application.set_identification import request_items
    from djlib.domain.request_contracts import RequestCreate

    url = (result.get("source") or {}).get("url")
    items = request_items(result.get("tracklist") or {}, url)
    if not items:
        raise AppError(
            "TRACKLIST_NOT_FOUND",
            "djlib listened to the set but could not name any of its songs, and nothing lists "
            "them.",
            details={"next": f"djlib set {shlex.quote(url or 'LINK')} --tracklist FILE"},
        )
    from djlib.domain.contracts import safe_text

    # A page read by an older djlib may hold control characters in its title.
    title = " ".join(safe_text(name).split())[:300] or "Set"
    digest = hashlib.sha256(json.dumps([title, url, items], sort_keys=True).encode()).hexdigest()
    return RequestCreate.model_validate(
        {"name": title, "items": items, "idempotency_key": f"identified:{digest[:16]}"}
    )


def identified_summary(job_id: str, result: dict) -> dict:
    """What `djlib set` reports about the identification it used."""
    from djlib.application.identification import summary

    found = summary(result)
    coverage = found["coverage"]
    kept = ("seconds_identified", "duration_seconds", "percent", "seconds_heard", "percent_heard")
    return {
        "coverage": {key: coverage[key] for key in kept},
        "counts": found["counts"],
        "tracklist_file": found["tracklist_file"],
        "job_id": job_id,
        "reused": found["reused"],
    }


def identify_result(job: dict) -> dict:
    """What `djlib identify` prints: the compact tracklist and what to do next."""
    from djlib.application.identification import next_steps, summary

    found = summary(job["result"])
    return {"job_id": job["job_id"], **found, "next": next_steps(found)}


def register_identify(app, client, emit, handled, panel=None):
    @app.command("identify", rich_help_panel=panel)
    @handled
    def identify(
        ctx: typer.Context,
        url: Annotated[
            str | None,
            typer.Argument(
                metavar="LINK",
                help="The set's YouTube or SoundCloud link, in single quotes; or paste for the "
                "link you copied.",
            ),
        ] = None,
        tracklist: list[Path] = typer.Option(
            None,
            "--tracklist",
            help="A tracklist for the set, e.g. from 1001Tracklists: a text file, or paste for "
            "the one you copied; repeatable. Remembered for the set.",
        ),
        again: bool = typer.Option(
            False, "--again", help="Listen again even if djlib identified this set before."
        ),
        recognizer: Recognizer | None = typer.Option(
            None,
            "--recognizer",
            help="Song recognition service: shazam (default), or licensed audd or acrcloud "
            "(they need API keys).",
        ),
        name: str | None = typer.Option(
            None, help="Name for the tracklist; defaults to the upload's title."
        ),
    ) -> None:
        """Track down every song in a YouTube or SoundCloud set.

        djlib reads the description, chapters and comments, checks MixesDB, then listens.

        Each track says how sure djlib is and why; IDs list likely candidates.

        A tracklist from 1001Tracklists (--tracklist paste, or a file) fills the gaps.

        Ctrl-C stops listening; running it again continues where it stopped.
        """
        from djlib.interfaces.set_sources import browser_only

        if url is None:
            raise nothing_given("identify")
        url = unquoted(url)
        if kind := typed(url):
            link, text = read_typed(kind)
            if link is None:
                raise nothing_in(kind, text, "identify")
            say_link(kind, link)
            url = link
        if (error := bare_link(url, "identify")) is not None:
            raise error
        browser_only(url, "identify")
        local = client(ctx)
        job = submit(
            local,
            url,
            tracklists=read_tracklists(tracklist),
            again=again,
            recognizer=recognizer.value if recognizer else None,
            name=name,
        )
        job = follow(local, job, url)
        result = identify_result({**job, "result": outcome(job)})
        reply = envelope(result)
        reply["warnings"] = warnings_of(job["result"])
        emit(reply)
