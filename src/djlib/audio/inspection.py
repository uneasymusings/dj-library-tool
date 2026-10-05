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
    bit_depth: int | None = None
    bitrate_bps: int | None = None
    codec_profile: str | None = None

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
        artist = title = []
        if media is not None and media.tags is not None:
            # WAV may expose ID3 frames even when easy=True; normalize both representations.
            artist = media.tags.get("artist") or media.tags.get("TPE1") or []
            title = media.tags.get("title") or media.tags.get("TIT2") or []
        found = (str(artist[0]) if artist else "", str(title[0]) if title else "")
        if path.suffix.lower() == ".wav" and not all(found):
            info = riff_info(path)
            found = (found[0] or info[0], found[1] or info[1])
        return found
    except (mutagen.MutagenError, ValueError, OSError):
        return "", ""


def riff_info(path: Path) -> tuple[str, str]:
    """Artist/title from a WAV LIST/INFO chunk, which many exporters write instead of ID3."""
    values: dict[bytes, str] = {}
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return "", ""
        for _ in range(64):  # bounded walk; audio data chunks are skipped, not read
            chunk = stream.read(8)
            if len(chunk) < 8:
                break
            ident, size = chunk[:4], int.from_bytes(chunk[4:], "little")
            if ident != b"LIST" or size > 1024 * 1024:
                stream.seek(size + (size & 1), 1)
                continue
            body = stream.read(size + (size & 1))
            offset = 4 if body[:4] == b"INFO" else len(body)
            while offset + 8 <= len(body):
                key = body[offset : offset + 4]
                length = int.from_bytes(body[offset + 4 : offset + 8], "little")
                raw = body[offset + 8 : offset + 8 + length].split(b"\0", 1)[0]
                try:
                    values[key] = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    values[key] = raw.decode("latin-1").strip()
                offset += 8 + length + (length & 1)
    return values.get(b"IART", ""), values.get(b"INAM", "")


def inspect_audio(path: Path, pcm: dict | None = None) -> Inspection:
    """Decode WAV locally; other formats require both FFmpeg and ffprobe.

    Claimed extension/bitrate alone is never a validation result. Full decoding is
    bounded by a timeout for external tools and streams WAV frames in fixed chunks.
    """
    before = path.stat()
    bit_depth = bitrate_bps = None
    codec_profile = None
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
                bit_depth = reader.getsampwidth() * 8
                bitrate_bps = rate * channels * bit_depth
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
                    "stream=codec_type,codec_name,profile,channels,sample_rate,bits_per_raw_sample,bits_per_sample,bit_rate:format=duration",
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
            bit_depth = (
                int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0) or None
            )
            bitrate_bps = int(audio.get("bit_rate") or 0) or None
            codec_profile = audio.get("profile")
            if pcm is not None:
                # One full decode both validates the audio and yields its exact PCM hash.
                from djlib.audio.fingerprint import pcm_hash

                pcm["sha256"] = pcm_hash(path)
            else:
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
        return Inspection(
            digest,
            after.st_size,
            duration,
            codec,
            channels,
            rate,
            artist,
            title,
            bit_depth,
            bitrate_bps,
            codec_profile,
        )
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
