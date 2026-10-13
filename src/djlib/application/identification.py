"""Identify a DJ set from its link: read the page, listen to the set, merge one tracklist.

Identification jobs run one at a time beside the Worker, never in it: listening to a set
takes minutes, and Shazam limits requests per address, so downloads and scans keep going
meanwhile. Each set has a folder, ``<workspace>/sets/<key>/``:

- page.json: what the page said (description, chapters, listener comments);
- listings.json: tracklists given for the set (e.g. copied from 1001Tracklists);
- probes.jsonl: one line per listening window, written the moment it is heard;
- tracklist.json and tracklist.txt: the merged result (the text works with `djlib set FILE`);
- audio/: the set's audio while it is listened to, deleted afterwards.

Probes are kept, so a paused, interrupted or rate-limited identification resumes where it
stopped, and merging new tracklists later never needs the audio again.
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import statistics
import time
import warnings
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from djlib.application.service import Application, add_event, require
from djlib.application.set_identification import (
    SOURCE_NAMES,
    build_tracklist,
    clock,
    listed_tracklist,
    segments,
    track_notes,
    tracklist_text,
)
from djlib.application.source_matching import tracklist_from_source
from djlib.application.tracklists import HEADING, SEPARATOR
from djlib.audio import windows
from djlib.domain.contracts import IdentifyRequest
from djlib.domain.errors import AppError
from djlib.persistence.models import Job, timestamp
from djlib.sources import recognition, web
from djlib.sources.recognition import Probe
from djlib.workspace import Workspace, atomic_json

logger = logging.getLogger(__name__)

KIND = "identify"
# Listening windows: 12 seconds every 30, starting 9 seconds in (recognition.placements).
WINDOW, STRIDE, OFFSET = 12.0, 30.0, 9.0
# Checking gaps spends at most half as many windows as the first listen.
REFINE_SHARE = 0.5
COMMENTS = 1000
# A page `djlib set` just read is not read again by the identification it starts.
PAGE_SECONDS = 900.0
POLL_SECONDS = 0.5
KEEP_AUDIO_ENV = "DJLIB_KEEP_SET_AUDIO"
# Failures after which trying again later continues from the windows already heard.
RESUMABLE = frozenset({"RECOGNITION_RATE_LIMITED", "RECOGNITION_UNAVAILABLE"})
STATUSES = ("confirmed", "likely", "weak", "conflict", "unidentified")
SET_HOSTS = ("youtube.com", "youtu.be", "soundcloud.com")
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NOTICE_FILE = "notices.json"
INSTALLER = (
    "curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh"
    " | sh"
)
RECOGNIZER_LABELS = {"shazam": "Shazam", "audd": "AudD", "acrcloud": "ACRCloud"}
UNOFFICIAL_SHAZAM = (
    "djlib uses Shazam's recognition service the way the Shazam app does. Apple doesn't offer "
    "it to other apps, so it may slow down or stop working. For a licensed service, set "
    "DJLIB_RECOGNIZER=audd with DJLIB_AUDD_TOKEN, or DJLIB_RECOGNIZER=acrcloud with "
    "DJLIB_ACRCLOUD_HOST, DJLIB_ACRCLOUD_KEY and DJLIB_ACRCLOUD_SECRET."
)


# -- links and folders -------------------------------------------------------------------------


def _domain(host: str, name: str) -> bool:
    return host == name or host.endswith("." + name)


def canonical_url(url: str) -> str:
    """One spelling per set, so every link to it shares one folder and one identification.

    YouTube links become ``https://www.youtube.com/watch?v=ID`` (``youtu.be``, ``&t=`` and
    playlist parameters dropped); SoundCloud links lose tracking parameters. Other sites are
    refused with the way forward.
    """
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is not None and _domain(host, "1001tracklists.com"):
        raise AppError(
            "SOURCE_BROWSER_ONLY",
            "1001Tracklists only shows tracklists to people in a browser. Copy the tracklist "
            "into a text file and give it with the set's YouTube or SoundCloud link: "
            "djlib identify SET_LINK --tracklist FILE.",
        )
    if (
        parts is None
        or parts.scheme not in {"http", "https"}
        or not any(_domain(host, known) for known in SET_HOSTS)
    ):
        raise AppError("SOURCE_UNSUPPORTED", "Use the YouTube or SoundCloud link of one set.")
    if _domain(host, "youtu.be") or _domain(host, "youtube.com"):
        video = ""
        if _domain(host, "youtu.be"):
            video = parts.path.strip("/").split("/")[0]
        else:
            video = (parse_qs(parts.query).get("v") or [""])[0]
            segments_ = [part for part in parts.path.split("/") if part]
            if not video and len(segments_) == 2 and segments_[0] in {"live", "embed", "v"}:
                video = segments_[1]
        if not YOUTUBE_ID.match(video):
            raise AppError(
                "SINGLE_SOURCE_REQUIRED",
                "Use the link of one YouTube video, not a playlist or channel page.",
            )
        canonical = f"https://www.youtube.com/watch?v={video}"
    else:
        path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
        if not path:
            raise AppError("SINGLE_SOURCE_REQUIRED", "Use the link of one SoundCloud set.")
        site = "on.soundcloud.com" if _domain(host, "on.soundcloud.com") else "soundcloud.com"
        canonical = f"https://{site}{path}"
    return web.validate_url(canonical)


def set_key(url: str) -> str:
    """A short, stable folder name for a set."""
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def set_folder(workspace: Workspace, url: str) -> Path:
    return workspace.sets / set_key(url)


def _json(path: Path) -> dict:
    """A small JSON file's object; a missing or unreadable one counts as empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_probes(folder: Path) -> list[dict]:
    """Every listening window heard for a set, in the order heard (a cut-off line is skipped)."""
    try:
        lines = (folder / "probes.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    probes = []
    for line in lines:
        try:
            probe = json.loads(line)
        except ValueError:
            continue
        if isinstance(probe, dict) and isinstance(probe.get("at"), int | float):
            probes.append(probe)
    return probes


def append_probe(folder: Path, probe: Probe, step: str, recognizer: str) -> dict:
    line = {"pass": step, "recognizer": recognizer, **probe.as_dict()}
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "probes.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, ensure_ascii=False) + "\n")
        stream.flush()
    return line


def finished(workspace: Workspace, url: str) -> dict | None:
    """The saved identification of this set (tracklist.json), or None."""
    try:
        folder = set_folder(workspace, url)
    except AppError:
        return None
    saved = _json(folder / "tracklist.json")
    return saved if saved.get("complete") and isinstance(saved.get("tracklist"), dict) else None


# -- pages read moments ago --------------------------------------------------------------------

PAGES: dict[str, tuple[float, dict]] = {}


def remember_page(page: dict, comments: int) -> None:
    """Keep a page read with its comments, so an identification started next reuses it."""
    if comments < COMMENTS or not isinstance(page, dict):
        return
    try:
        url = canonical_url(page.get("url") or "")
    except AppError:
        return
    now = time.monotonic()
    for key in [key for key, (at, _) in PAGES.items() if now - at > PAGE_SECONDS]:
        del PAGES[key]
    PAGES[url] = (now, page)


def recent_page(url: str) -> dict | None:
    found = PAGES.get(url)
    if found is None or time.monotonic() - found[0] > PAGE_SECONDS:
        return None
    return found[1]


# -- song recognition setup ----------------------------------------------------------------------


def recognizer(name: str | None = None) -> recognition.Recognizer:
    """The recognizer an identification uses (tests replace it; see conftest)."""
    return recognition.recognizer(name)


def shazam_installed() -> bool:
    """Whether shazamio imports here (it needs Python 3.12 wheels; see recognition.INSTALL)."""
    try:
        with warnings.catch_warnings():
            # pydub, which shazamio uses, has invalid escape sequences in its regexes.
            warnings.simplefilter("ignore")
            import shazamio  # noqa: F401
    except Exception:  # noqa: BLE001 - ImportError, or pydub without audioop on Python 3.13
        return False
    return True


def recognition_status(environ: Mapping[str, str] = os.environ) -> dict:
    """Doctor's song recognition row: which service, whether it can run, and the fix."""
    name = (environ.get("DJLIB_RECOGNIZER") or "shazam").strip().lower()
    status = {
        "recognizer": name,
        "label": RECOGNIZER_LABELS.get(name, name),
        "licensed": name != "shazam",
        "installed": True,
        "configured": True,
        "ready": False,
        "problem": None,
        "fix": None,
    }
    if name not in RECOGNIZER_LABELS:
        status.update(
            configured=False,
            problem=f"DJLIB_RECOGNIZER is {name!r}; djlib knows shazam, audd and acrcloud.",
            fix="unset DJLIB_RECOGNIZER",
        )
    elif name == "shazam" and not shazam_installed():
        status.update(installed=False, problem="Song recognition is not installed.", fix=INSTALLER)
    elif name == "audd" and not environ.get("DJLIB_AUDD_TOKEN"):
        status.update(
            configured=False,
            problem="AudD needs an API token.",
            fix="export DJLIB_AUDD_TOKEN=YOUR_TOKEN",
        )
    elif name == "acrcloud":
        names = ("DJLIB_ACRCLOUD_HOST", "DJLIB_ACRCLOUD_KEY", "DJLIB_ACRCLOUD_SECRET")
        if missing := [n for n in names if not environ.get(n)]:
            status.update(
                configured=False,
                problem="ACRCloud needs a project's host, key and secret.",
                fix=" ".join(f"export {n}=…" for n in missing),
            )
    status["ready"] = status["installed"] and status["configured"]
    return status


def check_ready(name: str | None) -> None:
    """Fail now, not minutes into a download, when this computer can't listen to a set."""
    recognizer(name)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AppError("DEPENDENCY_REQUIRED", "Song recognition requires FFmpeg and ffprobe.")
    if importlib.util.find_spec("yt_dlp") is None:  # it reads the page and fetches the audio
        raise AppError(
            "DEPENDENCY_REQUIRED",
            f"Listening to sets needs yt-dlp, which the djlib installer adds: {INSTALLER}",
        )


# -- submitting ----------------------------------------------------------------------------------


def submit(application: Application, body: IdentifyRequest) -> dict:
    """Queue an identification; reusing the idempotency key returns the same job."""
    url = canonical_url(body.url)
    payload = body.model_dump(mode="json", exclude={"idempotency_key"})
    # The jobs list shows a job's name.
    payload.update(url=url, name=body.name or url)
    if body.again or finished(application.workspace, url) is None:
        check_ready(body.recognizer)
    return application.submit(KIND, payload, body.idempotency_key)


# -- running -------------------------------------------------------------------------------------


class Stopped(Exception):
    """The job was paused, cancelled or handed back while it ran."""


@dataclass
class _Attempt:
    job_id: str
    generation: int
    request: dict
    folder: Path
    result: dict
    phase: str = ""
    heard: list[dict] = field(default_factory=list)
    phase_started: float = 0.0
    phase_done: int = 0
    refine_budget: int = 0
    progress: dict = field(default_factory=dict)


def _receipt_path(receipt: dict) -> Path:
    return Path(str(receipt.get("path") or ""))


class IdentifyRunner:
    """Runs identification jobs one at a time, beside the Worker."""

    def __init__(self, application: Application):
        self.app = application

    # Scheduling

    def recover(self) -> None:
        """Identifications that were running when the service stopped wait to resume."""
        with self.app.db.transaction() as session:
            for job in session.scalars(select(Job).where(Job.state == "running", Job.kind == KIND)):
                job.state, job.generation = "queued", job.generation + 1
                add_event(session, job, "recovered")

    async def run(self) -> None:
        self.recover()
        while True:
            try:
                attempt = self.claim()
            except Exception:  # noqa: BLE001 - a busy catalog must not end identification for good
                logger.exception("Could not pick the next identification")
                attempt = None
                await asyncio.sleep(1.0)
            if attempt is None:
                await asyncio.sleep(0.25)
                continue
            job_id, generation = attempt
            try:
                await self.execute(job_id, generation)
            except asyncio.CancelledError:
                # Service shutdown is a checkpoint: the job resumes when the service is back.
                self.checkpoint(job_id, generation)
                raise
            except Exception:
                logger.exception("Identification failed: %s", job_id)
                self.fail(
                    job_id,
                    generation,
                    AppError(
                        "JOB_FAILED",
                        "The identification stopped unexpectedly; try again. If it keeps "
                        "failing, the service log in the workspace's runtime folder says why.",
                        500,
                        True,
                    ),
                )

    def claim(self) -> tuple[str, int] | None:
        with self.app.db.transaction() as session:
            job = session.scalar(
                select(Job)
                .where(Job.state == "queued", Job.kind == KIND)
                .order_by(Job.updated_at, Job.created_at, Job.id)
                .limit(1)
            )
            if job is None:
                return None
            job.state, job.generation = "running", job.generation + 1
            job.result = {key: value for key, value in (job.result or {}).items() if key != "error"}
            add_event(session, job, "started")
            return job.id, job.generation

    def active(self, job_id: str, generation: int) -> bool:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            return job.state == "running" and job.generation == generation

    def checkpoint(self, job_id: str, generation: int) -> None:
        with self.app.db.transaction() as session:
            job = session.get(Job, job_id)
            if job is not None and job.state == "running" and job.generation == generation:
                job.state, job.generation = "queued", job.generation + 1
                add_event(session, job, "recovered")

    def fail(self, job_id: str, generation: int, error: AppError) -> None:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state == "running" and job.generation == generation:
                job.state = "failed"
                job.result = {**job.result, "error": error.as_dict()}
                add_event(session, job, "failed", {"code": error.code})

    def save(self, attempt: _Attempt, **fields) -> None:
        """Merge ``fields`` into the job's result (only while this attempt owns the job)."""
        with self.app.db.transaction() as session:
            job = require(session, Job, attempt.job_id)
            if job.state != "running" or job.generation != attempt.generation:
                return
            job.result = {**job.result, **fields}
            job.updated_at = timestamp()

    # Progress

    def enter(self, attempt: _Attempt, phase: str, **counts) -> None:
        attempt.phase = phase
        attempt.phase_started = time.monotonic()
        # Windows heard before a resume do not tell how fast this attempt goes.
        attempt.phase_done = counts.get("windows_done") or 0
        attempt.progress = {
            "phase": phase,
            "windows_done": counts.get("windows_done"),
            "windows_total": counts.get("windows_total"),
            "songs_heard": self.songs(attempt),
            "eta_seconds": None,
        }
        with self.app.db.transaction() as session:
            job = require(session, Job, attempt.job_id)
            if job.state != "running" or job.generation != attempt.generation:
                raise Stopped
            job.result = {**job.result, "progress": attempt.progress}
            add_event(session, job, "phase", {"phase": phase})

    def songs(self, attempt: _Attempt) -> int:
        found = segments(attempt.heard, window=WINDOW, stride=STRIDE)
        return sum(1 for song in found if song["strength"] != "weak")

    def heard(self, attempt: _Attempt, done: int, total: int) -> None:
        """Progress after each window: counts, songs so far and the time left."""
        now = time.monotonic()
        fresh = done - attempt.phase_done
        left = max(total - done, 0)
        if attempt.phase == "listening":
            left += attempt.refine_budget // 2  # checking gaps usually takes about this many
        eta = None
        if fresh >= 3:
            eta = round((now - attempt.phase_started) / fresh * left)
        attempt.progress = {
            "phase": attempt.phase,
            "windows_done": done,
            "windows_total": total,
            "songs_heard": self.songs(attempt),
            "eta_seconds": eta,
        }
        self.save(attempt, progress=attempt.progress)

    async def watch(
        self,
        attempt: _Attempt,
        work: Awaitable,
        tick: Callable[[], None] | None = None,
    ):
        """Await ``work``, stopping it promptly when the job is paused or cancelled."""
        task = asyncio.ensure_future(work)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
                if done:
                    return task.result()
                if not self.active(attempt.job_id, attempt.generation):
                    raise Stopped
                if tick is not None:
                    tick()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # The identification

    async def execute(self, job_id: str, generation: int) -> None:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            request, result = dict(job.request), dict(job.result or {})
        folder = set_folder(self.app.workspace, request["url"])
        attempt = _Attempt(job_id, generation, request, folder, result)
        try:
            outcome = await self.identify(attempt)
        except Stopped:
            with self.app.db.transaction() as session:
                cancelled = require(session, Job, job_id).state == "cancelled"
            if cancelled:
                self.drop_audio(folder)
            return
        except AppError as error:
            if error.code not in RESUMABLE:
                self.drop_audio(folder)
            self.fail(job_id, generation, self.explain(error, folder))
            return
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state != "running" or job.generation != generation:
                return
            job.state, job.outcome = "completed", "complete"
            job.result = outcome
            add_event(
                session,
                job,
                "completed",
                {"tracks": len(outcome["tracklist"]["tracks"]), "reused": outcome["reused"]},
            )

    async def identify(self, attempt: _Attempt) -> dict:
        request, folder = attempt.request, attempt.folder
        url = request["url"]
        folder.mkdir(parents=True, exist_ok=True)
        listings = self.listings(folder, request.get("tracklists") or [])
        saved = _json(folder / "tracklist.json")
        if saved.get("complete") and not request.get("again"):
            page = _json(folder / "page.json")
            self.enter(attempt, "merging")
            return self.merge(
                attempt,
                page,
                load_probes(folder),
                listings,
                saved.get("recognizer") or {},
                saved.get("duration"),
                reused=True,
            )
        if request.get("again") and not attempt.result.get("started_over"):
            # Once per job: a resumed "again" job keeps what it heard before the restart.
            for name in ("probes.jsonl", "tracklist.json", "tracklist.txt"):
                (folder / name).unlink(missing_ok=True)
            attempt.result["started_over"] = True
            self.save(attempt, started_over=True)
        listener = recognizer(request.get("recognizer"))
        attempt.heard = load_probes(folder)

        self.enter(attempt, "reading_page")
        page = recent_page(url)
        if page is None:
            page = await self.watch(attempt, web.inspect_source(url, COMMENTS))
        page = self.keep_page(folder, url, page)

        self.enter(attempt, "downloading", windows_done=0)
        audio = folder / "audio"

        def downloaded() -> None:
            try:
                size = web._folder_bytes(audio)
            except OSError:
                return
            attempt.progress = {**attempt.progress, "downloaded_bytes": size}
            self.save(attempt, progress=attempt.progress)

        receipt = await self.watch(attempt, web.fetch_set_audio(url, audio), downloaded)
        path = _receipt_path(receipt)
        seconds = await asyncio.to_thread(windows.duration, path)
        self.check_preview(page, receipt, seconds, folder)

        starts = recognition.placements(seconds, window=WINDOW, stride=STRIDE, offset=OFFSET)
        stored = load_probes(folder)
        first = [p for p in stored if p.get("pass") == "scan"]
        attempt.refine_budget = int(max(len(starts), len(first)) * REFINE_SHARE)
        done = len({round(p["at"], 3) for p in first} & set(starts))
        self.enter(attempt, "listening", windows_done=done, windows_total=len(starts))
        whole_file = getattr(listener, "scan_file", None) is not None
        if not (whole_file and any(p.get("recognizer") == listener.name for p in first)):
            await self.watch(
                attempt,
                recognition.scan(
                    path,
                    listener,
                    window=WINDOW,
                    stride=STRIDE,
                    offset=OFFSET,
                    on_probe=self.recorder(attempt, "scan", listener.name),
                    skip=[] if whole_file else [p["at"] for p in first],
                ),
            )

        stored = load_probes(folder)
        first = [p for p in stored if p.get("pass") == "scan"]
        checked = [p for p in stored if p.get("pass") == "refine"]
        budget = max(0, int(len(first) * REFINE_SHARE) - len(checked))
        self.enter(attempt, "checking_gaps", windows_done=0, windows_total=budget)
        if budget:
            await self.watch(
                attempt,
                recognition.refine(
                    path,
                    listener,
                    [Probe.from_dict(p) for p in stored],
                    window=WINDOW,
                    limit=budget,
                    on_probe=self.recorder(attempt, "refine", listener.name, total=budget),
                ),
            )

        self.enter(attempt, "merging")
        outcome = self.merge(
            attempt,
            page,
            load_probes(folder),
            listings,
            {"name": listener.name, "licensed": bool(listener.licensed)},
            seconds,
            reused=False,
        )
        self.drop_audio(folder)
        return outcome

    def recorder(self, attempt: _Attempt, step: str, name: str, total: int | None = None):
        def record(probe: Probe, done: int, planned: int) -> None:
            attempt.heard.append(append_probe(attempt.folder, probe, step, name))
            self.heard(attempt, done, total if total is not None else planned)

        return record

    def listings(self, folder: Path, given: list[dict]) -> list[dict]:
        """The tracklists given for this set: this request's, else the ones given before."""
        if given:
            kept = [{"source": t["source"], "text": t["text"]} for t in given if t.get("text")]
            atomic_json(folder / "listings.json", {"listings": kept, "given_at": _now()})
            return kept
        saved = _json(folder / "listings.json").get("listings")
        return [t for t in saved if isinstance(t, dict)] if isinstance(saved, list) else []

    def keep_page(self, folder: Path, url: str, page: dict) -> dict:
        duration = page.get("duration_seconds")
        kept = {
            "url": url,
            "provider": page.get("provider"),
            "source_id": page.get("source_id"),
            "title": str(page.get("title") or ""),
            "uploader": str(page.get("uploader") or ""),
            "duration_seconds": float(duration) if isinstance(duration, int | float) else None,
            "description": str(page.get("description") or ""),
            "chapters": [c for c in page.get("chapters") or [] if isinstance(c, dict)],
            "comments": [c for c in page.get("comments") or [] if isinstance(c, dict)],
            "read_at": _now(),
        }
        atomic_json(folder / "page.json", kept)
        return kept

    def check_preview(self, page: dict, receipt: dict, seconds: float, folder: Path) -> None:
        """SoundCloud Go+ uploads give everyone else a 30-second preview of the set."""
        expected = page.get("duration_seconds") or receipt.get("duration_seconds")
        if not isinstance(expected, int | float) or expected < 120:
            return
        if seconds >= expected * 0.5:
            return
        self.drop_audio(folder)
        raise AppError(
            "SET_PREVIEW_ONLY",
            f"The site only lets djlib hear {clock(seconds)} of this {clock(expected)} set (a "
            "preview, as SoundCloud gives for Go+ uploads), so djlib can't listen to it. Copy "
            "the set's tracklist (for example from 1001Tracklists) into a text file and run "
            f"djlib set FILE --source {page.get('url')}",
            details={"heard_seconds": round(seconds, 1), "set_seconds": float(expected)},
        )

    def merge(
        self,
        attempt: _Attempt,
        page: dict,
        probes: list[dict],
        listings: list[dict],
        listener: dict,
        duration: float | None,
        *,
        reused: bool,
    ) -> dict:
        url = attempt.request["url"]
        listed = [listed_tracklist(t["source"], t["text"], url) for t in listings]
        description = tracklist_from_source(page.get("description") or "", [])
        chapters = tracklist_from_source("", page.get("chapters") or [])
        if description:
            listed.append(listed_tracklist("description", description, url))
        if chapters and not _same_lines(chapters, description):
            listed.append(listed_tracklist("chapters", chapters, url))
        skipped = [
            {"source": source["source"], **line}
            for source in listed
            for line in source["skipped"]
            if line["reason"] != HEADING  # the set's name above its tracklist
        ][:50]
        listed = [source for source in listed if source["entries"]]
        if not isinstance(duration, int | float) or duration <= 0:
            duration = page.get("duration_seconds")
        tracklist = build_tracklist(
            duration=duration,
            probes=probes,
            listed=listed,
            comments=page.get("comments") or [],
            window=WINDOW,
            stride=_stride(probes),
        )
        counts = Counter(track["status"] for track in tracklist["tracks"])
        source = {
            "url": url,
            "provider": page.get("provider"),
            "title": page.get("title") or None,
            "uploader": page.get("uploader") or None,
            "duration_seconds": duration,
        }
        name = attempt.request.get("name")
        title = name if name and name != url else page.get("title") or "Set"
        folder = attempt.folder
        created = _now()
        atomic_json(
            folder / "tracklist.json",
            {
                "complete": True,
                "url": url,
                "key": folder.name,
                "source": source,
                "recognizer": listener,
                "duration": duration,
                "window": WINDOW,
                "created_at": created,
                "tracklist": tracklist,
            },
        )
        text = tracklist_text(tracklist)
        (folder / "tracklist.txt").write_text(f"{_heading(title, text)}\n{text}\n", "utf-8")
        notices = []
        if (
            attempt.request.get("recognizer")
            and listener.get("name")
            and attempt.request["recognizer"] != listener["name"]
        ):
            notices.append(
                f"This set was identified with {_label(listener['name'])} before, so that is "
                f"what you see; listen again to use {_label(attempt.request['recognizer'])} "
                "(djlib identify LINK --again)."
            )
        if listener.get("name") == "shazam" and self.first_shazam():
            notices.append(UNOFFICIAL_SHAZAM)
        return {
            "source": source,
            "recognizer": {
                "name": listener.get("name"),
                "licensed": bool(listener.get("licensed")),
            },
            "tracklist": tracklist,
            "counts": {status: counts.get(status, 0) for status in STATUSES},
            "tracklist_file": str(folder / "tracklist.txt"),
            "tracklist_json": str(folder / "tracklist.json"),
            "reused": reused,
            "notices": notices,
            "skipped_lines": skipped,
            "identified_at": created,
        }

    def first_shazam(self) -> bool:
        """True once per workspace: the first identification that leans on Shazam says so."""
        path = self.app.workspace.sets / NOTICE_FILE
        seen = _json(path)
        if seen.get("unofficial_shazam"):
            return False
        atomic_json(path, {**seen, "unofficial_shazam": _now()})
        return True

    def drop_audio(self, folder: Path) -> None:
        if os.environ.get(KEEP_AUDIO_ENV, "").strip() not in {"", "0"}:
            return
        shutil.rmtree(folder / "audio", ignore_errors=True)

    def explain(self, error: AppError, folder: Path) -> AppError:
        """The failure in plain words, with what to do next."""
        if error.code in RESUMABLE:
            heard = len(load_probes(folder))
            return AppError(
                error.code,
                f"{error.message} What djlib heard so far ({heard} listening windows) is "
                "saved, so trying again later picks up from there.",
                error.status,
                True,
                details={"windows_heard": heard, "resumes": True},
            )
        if error.code == "RECOGNIZER_NOT_CONFIGURED":
            return AppError(
                error.code,
                f"{error.message} djlib's background service reads these settings when it "
                "starts: after setting them, run djlib service stop, then try again.",
                error.status,
                error.retryable,
            )
        return error


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _label(name: str) -> str:
    return RECOGNIZER_LABELS.get(name, name)


def _stride(probes: list[dict]) -> float:
    """The first listen's spacing; windows checked in between must not shrink it."""
    times = sorted({p["at"] for p in probes if p.get("pass", "scan") == "scan"})
    steps = [b - a for a, b in zip(times, times[1:], strict=False) if b > a]
    return statistics.median(steps) if steps else STRIDE


def _lines(text: str) -> set[str]:
    return {re.sub(r"^\[[\d:]+\]\s*", "", line).casefold() for line in text.splitlines()}


def _same_lines(chapters: str, description: str) -> bool:
    """YouTube makes chapters from the description's times: the same list, said twice."""
    if not description:
        return False
    first, second = _lines(chapters), _lines(description)
    return len(first & second) >= 0.8 * min(len(first), len(second))


def _heading(title: str, text: str) -> str:
    """The set's title as the tracklist's first line, which `djlib set FILE` uses as its name.

    Above two or more timed lines any first line is a heading; otherwise one with an
    "Artist - Title" separator would be read as a track.
    """
    title = " ".join(title.split()) or "Set"
    if sum(1 for line in text.splitlines() if line.startswith("[")) < 2:
        title = SEPARATOR.sub(" · ", title)
    return title


# -- results for people and assistants -----------------------------------------------------------


def _clock(seconds) -> str | None:
    if not isinstance(seconds, int | float) or not math.isfinite(seconds):
        return None
    return clock(seconds)


def _candidate(candidate: dict) -> dict:
    artist, title = candidate.get("artist") or "", candidate.get("title") or ""
    version = candidate.get("version") or ""
    label = f"{artist} - {title}" if artist else title
    if version:
        label += f" ({version})"
    found = {
        "source": candidate.get("source"),
        "label": label,
        "artist": artist or None,
        "title": title,
        "version": version,
    }
    if candidate.get("source") == "audio":
        found.update(
            heard_by=_label(str(candidate.get("provider") or "")),
            windows=candidate.get("hits"),
            at=_clock(candidate.get("at")),
            strength=candidate.get("strength"),
        )
    elif candidate.get("source") == "comments":
        found.update(
            mentions=candidate.get("mentions"),
            likes=candidate.get("likes"),
            at=_clock(candidate.get("at")),
        )
    else:
        found["listed_in"] = SOURCE_NAMES.get(candidate.get("source"), candidate.get("source"))
    return found


def compact_track(track: dict) -> dict:
    """One track as people and assistants read it; an ID's candidates are never its name."""
    compact = {
        "n": track.get("position"),
        "start": _clock(track.get("start")),
        "approx": bool(track.get("approx")),
        "kind": track.get("kind"),
        "artist": track.get("artist"),
        "title": track.get("title"),
        "version": track.get("version") or "",
        "status": track.get("status"),
        "evidence": track_notes(track),
    }
    candidates = [_candidate(c) for c in (track.get("candidates") or [])[:3]]
    if candidates:
        compact["candidates"] = candidates
    return compact


def summary(result: dict) -> dict:
    """A finished identification without the raw evidence (tracklist_json keeps all of it)."""
    tracklist = result.get("tracklist") or {}
    coverage = tracklist.get("coverage") or {}
    source = result.get("source") or {}
    duration = coverage.get("duration") or source.get("duration_seconds")
    identified = coverage.get("seconds_identified") or 0
    heard = coverage.get("seconds_heard")  # None in a tracklist merged before it was kept
    percent = percent_heard = None
    if isinstance(duration, int | float) and duration > 0:
        percent = min(100, round(100 * identified / duration))
        if isinstance(heard, int | float):
            percent_heard = min(100, round(100 * heard / duration))
    listed = [
        entry
        for entry in tracklist.get("sources") or []
        if entry.get("source") not in {"audio", "comments"}
    ]
    return {
        "source": source,
        "recognizer": result.get("recognizer"),
        "duration": _clock(duration),
        "coverage": {
            "duration_seconds": duration,
            "seconds_identified": identified,
            "percent": percent,
            "seconds_heard": heard,
            "percent_heard": percent_heard,
            "gaps": [
                {"start": _clock(gap.get("start")), "end": _clock(gap.get("end"))}
                for gap in coverage.get("gaps") or []
            ],
        },
        "counts": {"tracks": len(tracklist.get("tracks") or []), **(result.get("counts") or {})},
        "tracks": [compact_track(track) for track in tracklist.get("tracks") or []],
        "unplaced": [
            {
                "label": _candidate(song)["label"],
                "heard_by": _label(str(song.get("provider") or "")),
                "at": _clock(song.get("at")),
            }
            for song in (tracklist.get("unplaced") or [])[:20]
        ],
        "sources": tracklist.get("sources") or [],
        "listed": bool(listed),
        "tracklist_file": result.get("tracklist_file"),
        "tracklist_json": result.get("tracklist_json"),
        "reused": bool(result.get("reused")),
        "notices": result.get("notices") or [],
    }


def job_view(job: dict) -> dict:
    """An identify job for assistants: the summary instead of the raw tracklist."""
    result = job.get("result")
    if job.get("kind") != KIND or not isinstance(result, dict) or "tracklist" not in result:
        return job
    extra = {key: result[key] for key in ("skipped_lines", "identified_at") if key in result}
    return {**job, "result": {**summary(result), **extra}}
