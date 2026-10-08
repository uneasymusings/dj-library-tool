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
import sys
from pathlib import Path
from urllib.parse import urlsplit

from djlib.domain.errors import AppError
from djlib.sources.runtimes import javascript_runtimes
from djlib.workspace import atomic_json

DOMAINS = ("youtube.com", "youtu.be", "soundcloud.com", "bandcamp.com")
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024


def validate_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        allowed = any(host == domain or host.endswith("." + domain) for domain in DOMAINS)
        if (
            parts.scheme != "https"
            or not allowed
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError
    except ValueError as exc:
        raise AppError(
            "SOURCE_UNSUPPORTED", "Use a public HTTPS YouTube, SoundCloud, or Bandcamp URL."
        ) from exc
    return value


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
    args: list[str], *, timeout: int, output_limit: int, download_dir: Path | None = None
) -> bytes:
    """Bound streams, runtime, and growth; cancellation also terminates decoder children."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
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
        while process.returncode is None:
            if (
                download_dir
                and sum(p.stat().st_size for p in download_dir.iterdir() if p.is_file())
                > MAX_DOWNLOAD_BYTES
            ):
                raise AppError("DOWNLOAD_SIZE_LIMIT", "The selected audio exceeded 500 MiB.")
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


async def inspect_source(url: str, comments: int = 0) -> dict:
    """A set's published description and chapters, plus up to ``comments`` listener comments.

    SoundCloud comments carry their position in the set (``start_time``); YouTube comments
    often write timestamps in their text. Both are untrusted evidence, not identification.
    """
    validate_url(url)
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
    return {
        "url": url,
        "provider": data.get("extractor_key"),
        "source_id": data.get("id"),
        "title": str(data.get("title") or "")[:2000],
        "description": str(data.get("description") or "")[:30000],
        "duration_seconds": data.get("duration"),
        "uploader": str(data.get("uploader") or "")[:1000],
        "chapters": [
            {
                "start_time": c.get("start_time"),
                "end_time": c.get("end_time"),
                "title": str(c.get("title") or "")[:1000],
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
    validate_url(url)
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
                "title": str(entry.get("title") or "")[:500],
                "uploader": str(entry.get("uploader") or entry.get("channel") or "")[:300],
                # SoundCloud uploads carry the publisher's artist credit.
                "artists": [str(name)[:300] for name in artists[:10] if isinstance(name, str)],
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
    validate_url(url)
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
                "text": str(comment["text"])[:2000],
                "start_time": float(start) if isinstance(start, int | float) else None,
                "like_count": likes if isinstance(likes, int) else None,
                "author": str(comment.get("author") or "")[:200],
            }
        )
    return comments
