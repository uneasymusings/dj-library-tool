"""Short decoded excerpts of a long recording, for song recognition.

FFmpeg decodes straight to a pipe; nothing is written next to the recording.
"""

import io
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

from djlib.domain.errors import AppError


def _tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AppError("DEPENDENCY_REQUIRED", "Song recognition requires FFmpeg and ffprobe.")


def duration(path: Path) -> float:
    """How long the recording is, in seconds."""
    _tools()
    try:
        reply = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_entries",
                "format=duration:stream=codec_type,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        data = json.loads(reply.stdout)
        value = (data.get("format") or {}).get("duration")
        if value in {None, "N/A"}:
            # Some WebM uploads only record a duration on the audio stream.
            streams = [s for s in data.get("streams") or [] if s.get("codec_type") == "audio"]
            value = streams[0].get("duration") if streams else None
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
        return seconds
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        raise AppError(
            "AUDIO_INVALID", "ffprobe could not tell how long this recording is."
        ) from exc


def window_wav(path: Path, start: float, length: float = 12.0, rate: int = 16000) -> bytes:
    """A mono 16-bit PCM WAV of [start, start + length) seconds of the recording."""
    if not (start >= 0 and 0 < length <= 60 and 8000 <= rate <= 48000):
        raise AppError("INPUT_INVALID", "Choose a window of up to 60 seconds inside the recording.")
    _tools()
    try:
        reply = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(rate),
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "AUDIO_INVALID", "FFmpeg could not decode this part of the recording."
        ) from exc
    pcm = reply.stdout[: int(length * rate) * 2]
    if len(pcm) < 2:
        raise AppError("AUDIO_WINDOW_EMPTY", "This part of the recording has no audio.")
    # A WAV written to a pipe cannot carry its final size; this header is complete.
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm[: len(pcm) // 2 * 2])
    return output.getvalue()
