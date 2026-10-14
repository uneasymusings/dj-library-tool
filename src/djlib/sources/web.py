"""Selected public web audio through an isolated, optional yt-dlp subprocess.

The adapter deliberately exposes metadata, not an acoustic identification claim.
It ignores host yt-dlp configuration and never loads browser cookies or plugins.
"""

import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from djlib.domain.contracts import safe_text
from djlib.domain.errors import AppError
from djlib.sources.runtimes import javascript_runtimes
from djlib.workspace import atomic_json

DOMAINS = ("youtube.com", "youtu.be", "soundcloud.com", "bandcamp.com")
# SoundCloud's players take any address in ?url= and yt-dlp would fetch it.
PLAYER_HOSTS = frozenset({"w.soundcloud.com", "p.soundcloud.com", "player.soundcloud.com"})
# Only these yt-dlp extractors run (never its generic one, which fetches any address).
EXTRACTORS = r"youtube(?::.*)?,youtubeytbe,soundcloud(?::.*)?,bandcamp(?::.*)?"
SHORT_HOST = "on.soundcloud.com"
SOUNDCLOUD_HOSTS = frozenset({"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"})
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024


def _carries_link(query: str) -> bool:
    """Whether a query passes on another address ("?url=http://…", "?u=//host/…")."""
    for _, value in parse_qsl(query, keep_blank_values=True):
        value = unquote(value).strip().lower()
        if "://" in value or value.startswith("//"):
            return True
    return False


def validate_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        allowed = any(host == domain or host.endswith("." + domain) for domain in DOMAINS)
        if (
            parts.scheme != "https"
            or not allowed
            or host in PLAYER_HOSTS
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or _carries_link(parts.query)
        ):
            raise ValueError
    except ValueError as exc:
        raise AppError(
            "SOURCE_UNSUPPORTED", "Use a public HTTPS YouTube, SoundCloud, or Bandcamp URL."
        ) from exc
    return value


def resolve_link(url: str, transport: httpx.BaseTransport | None = None) -> str:
    """A SoundCloud short link (on.soundcloud.com/…) as the full link it stands for; any
    other link as given. One request, redirects not followed: the answer must point at
    soundcloud.com, and its tracking parameters are dropped."""
    try:
        parts = urlsplit(str(url).strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return url
    if host != SHORT_HOST:
        return url
    validate_url(f"https://{SHORT_HOST}{parts.path}")
    try:
        with httpx.Client(transport=transport, timeout=15, follow_redirects=False) as client:
            response = client.get(f"https://{SHORT_HOST}{parts.path}")
    except httpx.HTTPError as exc:
        raise AppError(
            "SOURCE_FAILED",
            "SoundCloud did not open this short link just now; try again, or use the set's "
            "full link.",
            502,
            True,
        ) from exc
    try:
        target = urlsplit(response.headers.get("location") or "")
        target_host = (target.hostname or "").lower()
    except ValueError:
        target, target_host = None, ""
    if (
        target is None
        or response.status_code not in {301, 302, 303, 307, 308}
        or target.scheme != "https"
        or target_host not in SOUNDCLOUD_HOSTS
        or not target.path.strip("/")
    ):
        raise AppError(
            "SOURCE_UNSUPPORTED",
            "This SoundCloud short link doesn't lead to a set; use the set's full link.",
        )
    return validate_url(f"https://soundcloud.com{target.path}")


async def resolved(url: str) -> str:
    """``resolve_link`` without blocking, then checked (``validate_url``)."""
    return validate_url(await asyncio.to_thread(resolve_link, url))


def command() -> list[str]:
    if importlib.util.find_spec("yt_dlp") is None:
        raise AppError(
            "DEPENDENCY_REQUIRED", "Install the download extra: uv sync --extra download."
        )
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-playlist",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
        "--no-progress",
        "--use-extractors",
        EXTRACTORS,
    ]
    # An old Deno on PATH must not hide a supported Node installation.
    # EJS is installed by the download extra; no remote component flag is enabled.
    if runtime := javascript_runtimes()["selected"]:
        args.extend(["--js-runtimes", runtime])
    return args


UNAVAILABLE = (
    "unavailable",
    "not available",
    "not found",
    "http error 404",
    "has been removed",
    "no longer available",
    "drm protected",
    "geo restrict",
    "geo-restrict",
    "your country",
)


def provider_error(stderr: bytes) -> AppError:
    """Classify bounded diagnostics without reflecting publisher text or private URLs."""
    text = stderr.decode("utf-8", errors="replace").lower()
    # Warnings ("retrying…") can name problems the final error does not have.
    errors = [line for line in text.splitlines() if line.startswith("error:")]
    text = "\n".join(errors) or text
    if "no supported javascript runtime" in text or "javascript runtime is not supported" in text:
        return AppError(
            "JAVASCRIPT_RUNTIME_REQUIRED",
            "YouTube extraction needs Deno 2.3+ or Node 22+. Run doctor for runtime details.",
            502,
        )
    if any(marker in text for marker in ("sign in", "login required", "private video", "cookies")):
        return AppError(
            "SOURCE_AUTH_REQUIRED",
            "The provider requires authentication; cookies are unsupported.",
            502,
        )
    if "429" in text or "too many requests" in text:
        return AppError(
            "SOURCE_RATE_LIMITED", "The provider rate limited retrieval; retry later.", 502, True
        )
    if re.search(r"http error 5\d\d", text):
        return AppError(
            "SOURCE_FAILED", "The provider could not retrieve this public source.", 502, True
        )
    if any(marker in text for marker in UNAVAILABLE):
        # Removed, region-locked and DRM-protected uploads stay that way; retrying cannot help.
        return AppError(
            "SOURCE_UNAVAILABLE",
            "The public recording is unavailable here (removed, region-locked or DRM-protected).",
            502,
        )
    return AppError(
        "SOURCE_FAILED", "The provider could not retrieve this public source.", 502, True
    )


async def run(
    args: list[str],
    *,
    timeout: int,
    output_limit: int,
    download_dir: Path | None = None,
    max_bytes: int | None = None,
    stall_seconds: float | None = None,
    on_start: Callable[[int], object] | None = None,
) -> bytes:
    """Bound streams, runtime, and growth; cancellation also terminates decoder children.

    ``download_dir`` may hold at most ``max_bytes`` (MAX_DOWNLOAD_BYTES by default), and with
    ``stall_seconds`` it must grow at least that often. ``on_start`` gets the process ID,
    which is also its process group on POSIX.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    if on_start is not None:
        on_start(process.pid)
    output = bytearray()
    diagnostics = bytearray()

    async def read() -> None:
        assert process.stdout is not None
        while chunk := await process.stdout.read(65536):
            output.extend(chunk)
            if len(output) > output_limit:
                raise AppError(
                    "SOURCE_OUTPUT_LIMIT", "Source metadata exceeded the supported bound."
                )

    async def read_errors() -> None:
        assert process.stderr is not None
        while chunk := await process.stderr.read(65536):
            diagnostics.extend(chunk)
            del diagnostics[:-65536]

    async def monitor() -> None:
        limit = max_bytes or MAX_DOWNLOAD_BYTES
        size, grew = -1, time.monotonic()
        while process.returncode is None:
            if download_dir:
                now, current = time.monotonic(), _folder_bytes(download_dir)
                if current > limit:
                    raise AppError(
                        "DOWNLOAD_SIZE_LIMIT",
                        f"The selected audio exceeded {limit // (1024 * 1024)} MiB.",
                    )
                if current != size:
                    size, grew = current, now
                elif stall_seconds and now - grew > stall_seconds:
                    raise AppError(
                        "SOURCE_TIMEOUT",
                        "The download stopped making progress; what arrived is kept, so trying "
                        "again continues it.",
                        504,
                        True,
                    )
            await asyncio.sleep(0.25)

    reader = asyncio.create_task(read())
    error_reader = asyncio.create_task(read_errors())
    monitor_task = asyncio.create_task(monitor())
    waiter = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(timeout):
            done, _ = await asyncio.wait(
                [reader, error_reader, monitor_task, waiter], return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                task.result()
            await reader
            await error_reader
            if await waiter != 0:
                raise provider_error(bytes(diagnostics))
    except TimeoutError as exc:
        raise AppError("SOURCE_TIMEOUT", "Source retrieval timed out.", 504, True) from exc
    finally:
        if os.name == "posix":
            # The session belongs exclusively to this retrieval, including FFmpeg children.
            # macOS reports EPERM instead of ESRCH when the group has only exited, unreaped
            # members left, so both mean it is already gone.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            # Windows has no POSIX process group signals. taskkill /T stops the decoder tree.
            taskkill = shutil.which("taskkill")
            if taskkill:
                killer = await asyncio.create_subprocess_exec(
                    taskkill,
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            if process.returncode is None:
                process.kill()
        await process.wait()
        for task in (reader, error_reader, monitor_task, waiter):
            task.cancel()
        await asyncio.gather(reader, error_reader, monitor_task, waiter, return_exceptions=True)
    return bytes(output)


def _folder_bytes(folder: Path) -> int:
    """Bytes in the folder's files; yt-dlp's fragment and part files come and go meanwhile."""
    total = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except FileNotFoundError:
                continue
    return total


async def inspect_source(url: str, comments: int = 0) -> dict:
    """A set's published description and chapters, plus up to ``comments`` listener comments.

    SoundCloud comments carry their position in the set (``start_time``); YouTube comments
    often write timestamps in their text. Both are untrusted evidence, not identification.
    """
    url = await resolved(url)
    extra = []
    if comments:
        extra = [
            "--write-comments",
            "--extractor-args",
            f"youtube:max_comments={comments},all,100;comment_sort=top",
        ]
    raw = await run(
        [*command(), "--skip-download", *extra, "--dump-single-json", "--", url],
        timeout=240 if comments else 90,
        output_limit=32 * 1024 * 1024 if comments else 8 * 1024 * 1024,
    )
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("A metadata object is required.")
    except ValueError as exc:
        raise AppError("SOURCE_INVALID", "The source returned invalid metadata.") from exc
    if data.get("_type") in {"playlist", "multi_video"}:
        raise AppError(
            "SINGLE_SOURCE_REQUIRED", "Select one set or recording, not an artist or playlist page."
        )
    chapters = data.get("chapters") or []
    if not isinstance(chapters, list) or any(not isinstance(c, dict) for c in chapters[:500]):
        raise AppError("SOURCE_INVALID", "The source returned invalid chapter metadata.")
    # Publisher and listener text is cleaned where it enters djlib (see safe_text).
    return {
        "url": url,
        "provider": data.get("extractor_key"),
        "source_id": data.get("id"),
        "title": safe_text(data.get("title"))[:2000],
        "description": safe_text(data.get("description"), lines=True)[:30000],
        "duration_seconds": data.get("duration"),
        "uploader": safe_text(data.get("uploader"))[:1000],
        "chapters": [
            {
                "start_time": c.get("start_time"),
                "end_time": c.get("end_time"),
                "title": safe_text(c.get("title"))[:1000],
            }
            for c in chapters[:500]
        ],
        "identity_evidence": "publisher metadata; untrusted text; no audio recognition",
        "description_truncated": len(str(data.get("description") or "")) > 30000,
        "comments": _comments(data, comments) if comments else [],
    }


AUDIO_NAMES = ("audio.mp3", "audio.flac")  # MP3 since a11; FLAC receipts from earlier releases


async def download(url: str, destination: Path) -> tuple[Path, dict]:
    """Fetch one selected recording as an MP3 (copied when the source already is one)."""
    url = await resolved(url)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AppError("DEPENDENCY_REQUIRED", "Web audio acquisition requires FFmpeg and ffprobe.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = destination / "receipt.json"
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError
        except (ValueError, OSError) as exc:
            raise AppError(
                "RECEIPT_INVALID", "The saved download receipt is invalid; inspect staging."
            ) from exc
        if receipt.get("source_url") == url:
            for name in AUDIO_NAMES:
                if (destination / name).is_file():
                    return destination / name, receipt
    # Stable output names cannot be influenced by a publisher's title or template.
    await run(
        [
            *command(),
            "--format",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--max-filesize",
            str(MAX_DOWNLOAD_BYTES),
            "--match-filters",
            "duration <= 1800 & !is_live",
            "--no-overwrites",
            "--output",
            str(destination / "audio.%(ext)s"),
            "--",
            url,
        ],
        timeout=1200,
        output_limit=1024 * 1024,
        download_dir=destination,
    )
    path = destination / "audio.mp3"
    if not path.is_file():
        raise AppError(
            "DOWNLOAD_UNAVAILABLE", "No audio was produced; the source may exceed the limits."
        )
    receipt = {
        "kind": "web_audio",
        "source_url": url,
        "adapter": "yt-dlp",
        "format": "mp3",
        "transformation": "MP3 for DJ players (VBR V0, or copied when the source is MP3); "
        "source quality is unchanged",
        "source_quality": "unverified",
        "acoustic_identity_verified": False,
    }
    atomic_json(receipt_path, receipt)
    return path, receipt


SET_AUDIO_BYTES = 2200 * 1024 * 1024  # yt-dlp stops at 2 GiB; the rest is room for part files
SET_FIELDS = "%(.{id,title,uploader,duration,extractor_key,filepath})j"
# A six-hour set over a slow link takes a while; a download that stops growing fails sooner.
SET_DOWNLOAD_SECONDS = 4 * 3600
SET_STALL_SECONDS = 300


async def fetch_set_audio(url: str, destination: Path) -> dict:
    """A whole DJ set's audio, for song recognition only; it never enters the catalog.

    The provider's audio is kept as published (FFmpeg reads WebM, M4A and Opus). A receipt
    beside it lets a later scan of the same set reuse the file, and an interrupted download
    of the same set resumes.
    """
    url = await resolved(url)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cached := _saved_set(destination, url):
        return cached
    pending = destination / "fetching.json"
    state = _json_file(pending)
    if state.get("url") != url:
        # yt-dlp keeps and resumes files already there, so another set's audio must go first.
        for stale in destination.glob("set.*"):
            stale.unlink(missing_ok=True)
        (destination / "receipt.json").unlink(missing_ok=True)
        atomic_json(pending, {"url": url})
    else:
        # A download left running by a service that was killed would write into the same
        # part file as this one.
        _stop_orphan(state.get("pid"), destination)

    def started(pid: int) -> None:
        atomic_json(pending, {"url": url, "pid": pid})

    # Stable output names cannot be influenced by a publisher's title or template.
    raw = await run(
        [
            *command(),
            "--format",
            "bestaudio/best",
            "--max-filesize",
            "2G",
            # A missing piece would shorten the set and shift every later time; failing lets
            # a retry fetch it.
            "--abort-on-unavailable-fragments",
            # One set: a playlist or channel page saves nothing (and is not walked).
            "--playlist-items",
            "1",
            "--match-filters",
            "duration <= 21600 & !is_live & !playlist_id",
            "--print",
            f"after_move:{SET_FIELDS}",
            "--output",
            str(destination / "set.%(ext)s"),
            "--",
            url,
        ],
        timeout=SET_DOWNLOAD_SECONDS,
        output_limit=1024 * 1024,
        download_dir=destination,
        max_bytes=SET_AUDIO_BYTES,
        stall_seconds=SET_STALL_SECONDS,
        on_start=started,
    )
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    try:
        info = json.loads(lines[-1]) if lines else {}
        if not isinstance(info, dict):
            raise ValueError("A metadata object is required.")
    except ValueError as exc:
        raise AppError("SOURCE_INVALID", "The source returned invalid metadata.") from exc
    path = destination / Path(str(info.get("filepath") or "")).name
    if not path.name.startswith("set.") or not path.is_file():
        raise AppError(
            "DOWNLOAD_UNAVAILABLE",
            "No audio was saved; the set may be live, longer than six hours or over 2 GB. "
            "Select one set, not a playlist or channel page.",
        )
    duration = info.get("duration")
    receipt = {
        "url": url,
        "provider": info.get("extractor_key"),
        "source_id": info.get("id"),
        "title": safe_text(info.get("title"))[:2000],
        "uploader": safe_text(info.get("uploader"))[:1000],
        "duration_seconds": float(duration) if isinstance(duration, int | float) else None,
        "path": str(path),
    }
    atomic_json(destination / "receipt.json", receipt)
    pending.unlink(missing_ok=True)
    return receipt


def _stop_orphan(pid: object, destination: Path) -> None:
    """Stop a yt-dlp that still writes this set's audio after the service that ran it died."""
    if os.name != "posix" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return
    try:
        reply = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return
    # The set's own output path, as fetch_set_audio passes it.
    if "yt_dlp" in reply.stdout and str(destination / "set.%(ext)s") in reply.stdout:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def _saved_set(destination: Path, url: str) -> dict | None:
    receipt = _json_file(destination / "receipt.json")
    if receipt.get("url") != url:
        return None
    path = destination / Path(str(receipt.get("path") or "")).name
    if not path.name.startswith("set.") or not path.is_file():
        return None
    return {**receipt, "path": str(path)}


def _json_file(path: Path) -> dict:
    """A small cache file's object; an unreadable one counts as missing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


SEARCH_PREFIX = {"youtube": "ytsearch", "soundcloud": "scsearch"}


async def search(provider: str, query: str, limit: int = 8) -> list[dict]:
    """Public search results (metadata only) from YouTube or SoundCloud."""
    if provider not in SEARCH_PREFIX:
        raise AppError("INPUT_INVALID", "Search YouTube or SoundCloud.")
    query = " ".join(query.split())[:300]
    if not query:
        raise AppError("INPUT_INVALID", "Search for an artist and title.")
    raw = await run(
        [
            *command(),
            "--flat-playlist",
            "--dump-single-json",
            "--",
            f"{SEARCH_PREFIX[provider]}{max(1, min(limit, 20))}:{query}",
        ],
        timeout=90,
        output_limit=8 * 1024 * 1024,
    )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AppError("SOURCE_INVALID", "The search returned invalid metadata.") from exc
    results = []
    for entry in (data.get("entries") or [])[:20] if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        link = entry.get("webpage_url") or entry.get("url") or ""
        try:
            validate_url(link)
        except AppError:
            continue
        duration = entry.get("duration")
        artists = entry.get("artists") if isinstance(entry.get("artists"), list) else []
        results.append(
            {
                "provider": provider,
                "url": link,
                "title": safe_text(entry.get("title"))[:500],
                "uploader": safe_text(entry.get("uploader") or entry.get("channel"))[:300],
                # SoundCloud uploads carry the publisher's artist credit.
                "artists": [
                    safe_text(name)[:300] for name in artists[:10] if isinstance(name, str)
                ],
                "duration": float(duration) if isinstance(duration, int | float) else None,
                "view_count": entry.get("view_count")
                if isinstance(entry.get("view_count"), int)
                else None,
            }
        )
    return results


PROBE_SECONDS = 30
GONE = frozenset({"SOURCE_UNAVAILABLE", "SOURCE_AUTH_REQUIRED"})


async def probe(url: str) -> dict:
    """Whether a recording can be fetched now: yt-dlp picks its audio without downloading."""
    url = await resolved(url)
    raw = await run(
        [
            *command(),
            "--format",
            "bestaudio/best",
            "--skip-download",
            "--dump-single-json",
            "--",
            url,
        ],
        timeout=PROBE_SECONDS,
        output_limit=8 * 1024 * 1024,
    )
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("A metadata object is required.")
    except ValueError as exc:
        raise AppError("SOURCE_INVALID", "The source returned invalid metadata.") from exc
    duration = data.get("duration")
    return {
        "url": url,
        "duration": float(duration) if isinstance(duration, int | float) else None,
    }


async def availability(urls: list[str]) -> dict[str, dict]:
    """Probe the URLs at once: ``available`` True, False (gone for good) or None (unknown).

    A timeout or rate limit says nothing about the upload, so it stays unknown.
    """
    found = await asyncio.gather(*(probe(url) for url in urls), return_exceptions=True)
    checked = {}
    for url, outcome in zip(urls, found, strict=True):
        if isinstance(outcome, AppError):
            gone = outcome.code in GONE
            checked[url] = {"available": False if gone else None, "reason": outcome.message}
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            checked[url] = {"available": True}
    return checked


def _comments(data: dict, limit: int) -> list[dict]:
    comments = []
    for comment in (data.get("comments") or [])[:limit]:
        if not isinstance(comment, dict) or not comment.get("text"):
            continue
        start = comment.get("start_time")
        likes = comment.get("like_count")
        comments.append(
            {
                "text": safe_text(comment["text"], lines=True)[:2000],
                "start_time": float(start) if isinstance(start, int | float) else None,
                "like_count": likes if isinstance(likes, int) else None,
                "author": safe_text(comment.get("author"))[:200],
            }
        )
    return comments
