"""`djlib identify LINK`: every song in a YouTube or SoundCloud set, with how sure djlib is.

The background service does the listening (an identify job); this command shows how far it
got and then the tracklist. `djlib set LINK` uses the same identification when a set's own
tracklist is missing or has IDs.
"""

import contextlib
import shlex
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from djlib.domain.errors import AppError
from djlib.interfaces import terminal
from djlib.interfaces.envelope import envelope
from djlib.interfaces.terminal import plural

PHASES = {
    "queued": "Starting",
    "reading_page": "Reading the set's page and comments",
    "downloading": "Downloading the set's audio",
    "listening": "Listening",
    "checking_gaps": "Checking the transitions",
    "merging": "Putting the tracklist together",
}
EVENT_SECONDS = 15
POLL_SECONDS = 0.5
TRACKLIST_BYTES = 1_000_000
# How long a listen takes, said once before `djlib set LINK` starts one.
LISTEN_TIME = "An hour-long set usually takes 5–10 minutes"
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


def read_text_file(path: Path, limit: int = TRACKLIST_BYTES) -> str:
    """A tracklist file's text: a regular file of at most ``limit`` bytes."""
    try:
        if not path.is_file():
            raise AppError("INPUT_INVALID", f"{path} is not a text file.")
        if path.stat().st_size > limit:
            raise AppError(
                "INPUT_INVALID", f"{path} is over {limit // 1_000_000} MB; a tracklist is smaller."
            )
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AppError("INPUT_INVALID", f"Could not read {path} as a UTF-8 text file.") from exc


def read_tracklists(paths: list[Path] | None) -> list[dict]:
    found = []
    for path in paths or []:
        path = path.expanduser()
        text = read_text_file(path)
        if not text.strip():
            raise AppError("INPUT_INVALID", f"{path} is empty; paste the set's tracklist into it.")
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


def waiting_text(waiting: dict) -> str:
    """ "Shazam is slow, waiting (Ctrl-C is safe)": no countdown, since the wait has no known
    end, and stopping keeps what was heard."""
    service = waiting.get("service") or "Song recognition"
    slow = "is slow" if waiting.get("reason") == "rate_limited" else "isn't answering"
    return f"{service} {slow}, waiting (Ctrl-C is safe)"


def _where(phase: str | None, progress: dict) -> str:
    """How far the phase got: set time while listening ("15:30 of 42:05"), else a count."""
    from djlib.interfaces.set_sources import clock

    done, total = progress.get("windows_done") or 0, progress.get("windows_total")
    if phase == "listening":
        at, length = progress.get("position_seconds"), progress.get("duration_seconds")
        if isinstance(at, int | float) and isinstance(length, int | float) and length > 0:
            return f"{clock(at)} of {clock(length)}"
        return f"{done} of {total} parts" if total else ""
    if phase == "checking_gaps" and total:
        return f"{done} of {total}"
    if phase == "downloading" and progress.get("downloaded_bytes"):
        return f"{progress['downloaded_bytes'] / 1_000_000:.0f} MB"
    return ""


def progress_line(job: dict, width: int = 78) -> str:
    """ "Listening… 15:30 of 42:05 · 5 songs so far", in at most ``width`` columns.

    Progress is the set's own time, never a countdown: Shazam's waits make any guess of the
    time left wrong. While Shazam makes djlib wait, the line says so instead.
    """
    from rich.cells import cell_len

    progress = (job.get("result") or {}).get("progress") or {}
    phase = progress.get("phase") if job.get("state") == "running" else job.get("state")
    if phase == "queued" and progress.get("ahead"):
        return "Waiting for the set before it to finish…"
    text = PHASES.get(phase or "", "Identifying the set") + "…"
    where = _where(phase, progress)
    songs = ""
    if phase in {"listening", "checking_gaps", "merging"} and progress.get("songs_heard"):
        songs = plural(int(progress["songs_heard"]), "song")
    waiting = progress.get("waiting") if phase in {"listening", "checking_gaps"} else None
    if isinstance(waiting, dict):
        calm = waiting_text(waiting)
        # Fullest first, the first that fits; that Ctrl-C is safe matters more than the count.
        choices = [
            [where, songs, calm],
            [where, calm],
            [where, calm.removesuffix(" (Ctrl-C is safe)")],
        ]
    else:
        choices = [[where, f"{songs} so far" if songs else ""], [where, songs], [where]]
    for parts in choices:
        line = f"{text} {' · '.join(part for part in parts if part)}".rstrip()
        if cell_len(line) <= width:
            return line
    return line


def fit(term) -> int:
    """The progress line's room: the terminal's width less the spinner, so it never wraps."""
    return max(term.err.width - 2, 20)


def follow(local, job: dict, url: str | None = None, reason: str | None = None) -> dict:
    """Wait for an identification, showing progress; Ctrl-C cancels it.

    A terminal gets a live status line; scripts and assistants get a JSON event on stderr
    when the job starts (with ``identify_reason`` when `djlib set` chose to listen), at each
    phase and at most every 15 seconds in between.
    """
    from djlib.interfaces.rekordbox_cli import event

    term = terminal.current()
    job_id = job["job_id"]
    live = None
    status = None
    if term.json:
        why = {"identify_reason": reason} if reason else {}
        event("identify_started", job_id=job_id, url=url, **why)
    else:
        status = term.err.status(f"[bold]{progress_line(job, fit(term))}", spinner_style="accent")
        live = status.__enter__()
    last_phase, last_event = None, 0.0
    try:
        while True:
            progress = (job.get("result") or {}).get("progress") or {}
            phase = progress.get("phase") if job.get("state") == "running" else job.get("state")
            if live is not None:
                live.update(f"[bold]{progress_line(job, fit(term))}")
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


def listen_reason(
    choice: bool | None, text: str, url: str, workspace, *, complete: bool = True
) -> str | None:
    """Why `djlib set LINK` listens to the set, or None when it doesn't: "asked" (--identify),
    or by default "no_tracklist", "incomplete" (MixesDB marks some tracklists so), "ids" when
    the tracklist has IDs, or "identified_before" (that costs nothing)."""
    from djlib.application.identification import finished
    from djlib.application.tracklists import parse_tracklist

    if choice is not None:
        return "asked" if choice else None
    if not text.strip():
        return "no_tracklist"
    if not complete:
        return "incomplete"
    items, _ = parse_tracklist(text, url)
    if any(item.kind == "unknown" for item in items):
        return "ids"
    return "identified_before" if finished(workspace, url) is not None else None


def wanted(choice: bool | None, text: str, url: str, workspace, *, complete: bool = True) -> bool:
    """Whether `djlib set LINK` listens to the set (see ``listen_reason``)."""
    return listen_reason(choice, text, url, workspace, complete=complete) is not None


def listening_notice(reason: str | None, text: str, url: str, workspace) -> str | None:
    """What `djlib set LINK` says before it listens: why, and how long that takes. None when
    there is nothing new to hear (a set identified before shows at once)."""
    from djlib.application.identification import finished
    from djlib.application.tracklists import parse_tracklist

    if reason in {None, "identified_before"} or finished(workspace, url) is not None:
        return None
    # Without a tracklist, skipping the listen leaves nothing to check.
    carry_on = "Ctrl-C stops it, and the same command carries on"
    if reason == "no_tracklist":
        why = "The page has no tracklist, so djlib is listening to the set to find the songs."
        return f"{why} {LISTEN_TIME}; {carry_on}."
    if reason == "ids":
        ids = sum(item.kind == "unknown" for item in parse_tracklist(text, url)[0])
        them = "it" if ids == 1 else "them"
        why = (
            f"The tracklist has {plural(ids, 'ID')}, so djlib is listening to the set to name "
            f"{them}."
        )
    elif reason == "incomplete":
        why = "MixesDB marks its tracklist incomplete, so djlib is listening to the set."
    else:
        return f"djlib is listening to the set. {LISTEN_TIME}; {carry_on}."
    return f"{why} {LISTEN_TIME}; --no-identify skips it."


def listen(
    local,
    url: str,
    tracklists: list[dict] | None = None,
    name: str | None = None,
    *,
    reason: str | None = None,
    notice: str | None = None,
):
    """Identify a set and wait for it: ``{"job_id", "result"}`` (raises why it stopped).
    A terminal first gets ``notice`` (``listening_notice``), once the listen is queued."""
    job = submit(local, url, tracklists=tracklists, name=name)
    term = terminal.current()
    if notice and not term.json:
        from rich.text import Text

        # Why on the first line, how long on the next.
        why, _, how_long = notice.partition(". ")
        said = Text(f"{why}.") if how_long else Text(why)
        if how_long:
            said.append(f"\n{how_long}", style="muted")
        terminal.hanging(term.err, Text(term.glyph("run"), style="accent"), said)
    job = follow(local, job, url, reason)
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
        url: Annotated[str, typer.Argument(help="The set's YouTube or SoundCloud link.")],
        tracklist: list[Path] = typer.Option(
            None,
            "--tracklist",
            help="A tracklist for the set, e.g. copied from 1001Tracklists; repeatable. "
            "Remembered for the set.",
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

        A tracklist from 1001Tracklists (--tracklist FILE) fills the gaps.

        Ctrl-C stops listening; running it again continues where it stopped.
        """
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
