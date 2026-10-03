"""Validate complete audio and measure properties before accepting a local asset."""

import hashlib
import json
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import mutagen

from djlib.domain.errors import AppError

SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a"})


@dataclass(frozen=True)
class Inspection:
    sha256: str
    size_bytes: int
    duration_seconds: float
    codec: str
    channels: int
    sample_rate: int
    artist: str
    title: str

    def as_dict(self) -> dict:
        return asdict(self)


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def labels(path: Path) -> tuple[str, str]:
    try:
        media = mutagen.File(path, easy=True)
        if media is None or media.tags is None:
            return "", ""
        # WAV may expose ID3 frames even when easy=True; normalize both representations.
        artist = media.tags.get("artist") or media.tags.get("TPE1") or []
        title = media.tags.get("title") or media.tags.get("TIT2") or []
        return str(artist[0]) if artist else "", str(title[0]) if title else ""
    except (mutagen.MutagenError, ValueError, OSError):
        return "", ""


def inspect_audio(path: Path) -> Inspection:
    """Decode WAV locally; other formats require both FFmpeg and ffprobe.

    Claimed extension/bitrate alone is never a validation result. Full decoding is
    bounded by a timeout for external tools and streams WAV frames in fixed chunks.
    """
    before = path.stat()
    if before.st_size > 2 * 1024 * 1024 * 1024:
        raise AppError("AUDIO_SIZE_LIMIT", "Audio inspection is limited to 2 GiB per file.")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise AppError("FORMAT_UNSUPPORTED", "This audio format is not supported.")
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as reader:
                frames, rate, channels = (
                    reader.getnframes(),
                    reader.getframerate(),
                    reader.getnchannels(),
                )
                expected = frames * channels * reader.getsampwidth()
                if not rate or frames / rate > 12 * 60 * 60:
                    raise AppError("DURATION_INVALID", "Media duration exceeds twelve hours.")
                decoded = 0
                while chunk := reader.readframes(16_384):
                    decoded += len(chunk)
                if decoded != expected or not frames or not rate or not channels:
                    raise AppError("AUDIO_INCOMPLETE", "The WAV payload is empty or truncated.")
                duration, codec = frames / rate, f"pcm_{reader.getsampwidth() * 8}"
        else:
            if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
                raise AppError("DEPENDENCY_REQUIRED", "This format requires ffprobe and FFmpeg.")
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_entries",
                    "stream=codec_type,codec_name,channels,sample_rate:format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
            data = json.loads(probe.stdout)
            audio = next(s for s in data["streams"] if s.get("codec_type") == "audio")
            duration = float(data["format"]["duration"])
            channels, rate = int(audio["channels"]), int(audio["sample_rate"])
            codec = audio["codec_name"]
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(path),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                timeout=120,
                check=True,
            )
        if duration <= 0 or duration > 12 * 60 * 60:
            raise AppError("DURATION_INVALID", "Media duration is outside the supported bound.")
        artist, title = labels(path)
        digest = checksum(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise AppError(
                "FILE_CHANGED", "The source changed during inspection; retry after it settles."
            )
        return Inspection(digest, after.st_size, duration, codec, channels, rate, artist, title)
    except AppError:
        raise
    except (
        EOFError,
        OSError,
        ValueError,
        KeyError,
        StopIteration,
        wave.Error,
        subprocess.SubprocessError,
    ) as exc:
        raise AppError(
            "AUDIO_INVALID", "The file could not be completely decoded as audio."
        ) from exc
