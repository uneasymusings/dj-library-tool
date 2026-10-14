"""Short decoded excerpts of a long recording, for song recognition.

``SetAudio`` decodes a set once, in the background, to 16 kHz mono PCM beside it
(``<name>.pcm``, deleted with the set's audio) and cuts windows from that in-process.
``window_wav`` cuts a single window straight from the recording through a pipe.
"""

import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import threading
import wave
from pathlib import Path

from djlib.domain.errors import AppError

CHUNK = 1 << 16
# A window further than this past the decoded audio is cut on its own instead of waiting.
LEAD_SECONDS = 600.0
WAIT_SECONDS = 60.0  # the longest a window waits for the decoding to reach it
# Room left on the disk beside a decoding (the catalog and the rest of the workspace need it).
SPARE_BYTES = 200 * 1024 * 1024


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


def _decoding(path: Path, rate: int, seek: list[str] | None = None) -> list[str]:
    """FFmpeg writing the first audio stream as mono 16-bit PCM at ``rate`` to stdout."""
    return [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        *(seek or []),
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
    ]


def _check(start: float, length: float, rate: int) -> None:
    if not (start >= 0 and 0 < length <= 60 and 8000 <= rate <= 48000):
        raise AppError("INPUT_INVALID", "Choose a window of up to 60 seconds inside the recording.")


def _wav(pcm: bytes, rate: int) -> bytes:
    if len(pcm) < 2:
        raise AppError("AUDIO_WINDOW_EMPTY", "This part of the recording has no audio.")
    # A WAV written to a pipe cannot carry its final size; this header is complete.
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm[: len(pcm) // 2 * 2])
    return output.getvalue()


def window_wav(path: Path, start: float, length: float = 12.0, rate: int = 16000) -> bytes:
    """A mono 16-bit PCM WAV of [start, start + length) seconds of the recording."""
    _check(start, length, rate)
    _tools()
    try:
        reply = subprocess.run(
            _decoding(path, rate, ["-ss", f"{start:.3f}", "-t", f"{length:.3f}"]),
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "AUDIO_INVALID", "FFmpeg could not decode this part of the recording."
        ) from exc
    return _wav(reply.stdout[: int(length * rate) * 2], rate)


class SetAudio:
    """A set's audio decoded once to mono 16-bit PCM beside it, cut into WAV windows.

    FFmpeg decodes in the background from the first window asked for; a window waits only
    for its own samples. A finished decoding is kept for the next pass (until the recording
    changes); an unfinished one is deleted on ``close``. When decoding fails, stalls, is far
    behind or would not fit on the disk (``seconds`` of audio take 32 kB a second), a window
    is cut straight from the recording instead.
    """

    def __init__(self, path: Path, rate: int = 16000, seconds: float | None = None):
        self.path, self.rate, self.seconds = Path(path), rate, seconds
        self.pcm = self.path.with_name(self.path.name + ".pcm")
        self.part = self.path.with_name(self.path.name + ".pcm.part")
        self.changed = threading.Condition()
        self.started = self.finished = self.complete = self.closed = False
        self.decoded = 0  # bytes of PCM written so far
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "SetAudio":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def window(self, start: float, length: float = 12.0) -> bytes:
        """A mono 16-bit PCM WAV of [start, start + length) seconds, to the sample."""
        _check(start, length, self.rate)
        first = round(start * self.rate) * 2
        end = first + round(length * self.rate) * 2
        try:
            stream = self._open(end)
            if stream is not None:
                with stream:
                    stream.seek(first)
                    return _wav(stream.read(end - first), self.rate)
        except OSError:
            pass
        return window_wav(self.path, start, length, self.rate)

    def close(self) -> None:
        """Stop decoding; a decoding that did not finish is deleted."""
        with self.changed:
            self.closed = True
            self.changed.notify_all()
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
        if self.thread is not None:
            self.thread.join()
        if not self.complete:
            with contextlib.suppress(OSError):  # Windows keeps a file a reader has open
                self.part.unlink(missing_ok=True)

    def _open(self, end: int):
        """The decoded samples up to ``end``, opened for reading; None to cut them alone."""
        with self.changed:
            if not self.closed:
                self._start()
            behind = end - self.decoded > LEAD_SECONDS * self.rate * 2
            if behind and not (self.finished or self.closed):
                return None
            ready = self.changed.wait_for(
                lambda: self.closed or self.finished or self.decoded >= end, WAIT_SECONDS
            )
            if self.closed:
                raise AppError("AUDIO_INVALID", "Listening to this recording has stopped.")
            if not ready or (self.decoded < end and not self.complete):
                return None
            return (self.pcm if self.complete else self.part).open("rb")

    def _start(self) -> None:
        """Begin decoding, or take the decoding a previous pass finished."""
        if self.started:
            return
        _tools()
        self.started = True
        try:
            if self.pcm.is_file() and self.pcm.stat().st_mtime >= self.path.stat().st_mtime:
                self.decoded, self.finished = self.pcm.stat().st_size, True
                self.complete = True
                return
            if not self._room():
                self.finished = True
                return
            sink = self.part.open("wb")
        except OSError:
            self.finished = True
            return
        try:
            self.process = subprocess.Popen(
                _decoding(self.path, self.rate),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            sink.close()
            self.finished = True
            return
        self.thread = threading.Thread(target=self._pump, args=(sink,), daemon=True)
        self.thread.start()

    def _room(self) -> bool:
        """Whether the whole decoding fits on the disk with SPARE_BYTES to spare."""
        try:
            seconds = self.seconds or duration(self.path)
            free = shutil.disk_usage(self.part.parent).free
        except (OSError, AppError):
            return True
        return free >= seconds * self.rate * 2 + SPARE_BYTES

    def _pump(self, sink) -> None:
        process, written = self.process, False
        try:
            with sink:
                while chunk := process.stdout.read1(CHUNK):
                    sink.write(chunk)
                    sink.flush()
                    with self.changed:
                        self.decoded += len(chunk)
                        self.changed.notify_all()
            written = True
        except OSError:  # e.g. a full disk: windows are then cut on their own
            process.kill()
            # Free the space at once: the catalog shares this disk.
            with contextlib.suppress(OSError):
                self.part.unlink(missing_ok=True)
        finally:
            process.stdout.close()
            complete = process.wait() == 0 and written
            with self.changed:
                if complete:
                    try:
                        os.replace(self.part, self.pcm)
                    except OSError:
                        complete = False
                self.finished, self.complete = True, complete
                self.changed.notify_all()
