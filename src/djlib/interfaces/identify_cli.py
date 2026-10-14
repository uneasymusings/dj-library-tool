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
