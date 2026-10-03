"""Selected public web audio through an isolated, optional yt-dlp subprocess.

The adapter deliberately exposes metadata, not an acoustic identification claim.
It ignores host yt-dlp configuration and never loads browser cookies or plugins.
"""

import asyncio
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from djlib.domain.errors import AppError
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
    return [
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


async def run(
    args: list[str], *, timeout: int, output_limit: int, download_dir: Path | None = None
) -> bytes:
    """Bound output, runtime, and staging growth; cancellation terminates the child."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    output = bytearray()

    async def read() -> None:
        assert process.stdout is not None
        while chunk := await process.stdout.read(65536):
            output.extend(chunk)
            if len(output) > output_limit:
                raise AppError(
                    "SOURCE_OUTPUT_LIMIT", "Source metadata exceeded the supported bound."
                )

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
    monitor_task = asyncio.create_task(monitor())
    waiter = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(timeout):
            done, _ = await asyncio.wait(
                [reader, monitor_task, waiter], return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                task.result()
            await reader
            if await waiter != 0:
                raise AppError(
                    "SOURCE_FAILED",
                    "The provider could not retrieve this public source.",
                    502,
                    True,
                )
    except TimeoutError as exc:
        raise AppError("SOURCE_TIMEOUT", "Source retrieval timed out.", 504, True) from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        for task in (reader, monitor_task, waiter):
            task.cancel()
        await asyncio.gather(reader, monitor_task, waiter, return_exceptions=True)
    return bytes(output)


async def inspect_source(url: str) -> dict:
    validate_url(url)
    raw = await run(
        [*command(), "--skip-download", "--dump-single-json", "--", url],
        timeout=90,
        output_limit=8 * 1024 * 1024,
    )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AppError("SOURCE_INVALID", "The source returned invalid metadata.") from exc
    if data.get("_type") in {"playlist", "multi_video"}:
        raise AppError(
            "SINGLE_SOURCE_REQUIRED", "Select one set or recording, not an artist or playlist page."
        )
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
            for c in (data.get("chapters") or [])[:500]
        ],
        "identity_evidence": "publisher metadata; untrusted text; no audio recognition",
        "description_truncated": len(str(data.get("description") or "")) > 30000,
    }


async def download(url: str, destination: Path) -> tuple[Path, dict]:
    validate_url(url)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AppError("DEPENDENCY_REQUIRED", "Web audio acquisition requires FFmpeg and ffprobe.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = destination / "receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("source_url") == url and (destination / "audio.flac").is_file():
            return destination / "audio.flac", receipt
    # Stable output names cannot be influenced by a publisher's title or template.
    await run(
        [
            *command(),
            "--format",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "flac",
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
    path = destination / "audio.flac"
    if not path.is_file():
        raise AppError(
            "DOWNLOAD_UNAVAILABLE", "No audio was produced; the source may exceed the limits."
        )
    receipt = {
        "kind": "web_audio",
        "source_url": url,
        "adapter": "yt-dlp",
        "transformation": "decoded to FLAC for compatibility; source quality is unchanged",
        "source_quality": "unverified",
        "acoustic_identity_verified": False,
    }
    atomic_json(receipt_path, receipt)
    return path, receipt
