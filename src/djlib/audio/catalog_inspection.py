"""Stable catalog inspections and exact decoded-payload comparison, not recognition."""

import hashlib
import wave
from pathlib import Path

from djlib.audio.file_identity import path_snapshot
from djlib.audio.fingerprint import pcm_hash
from djlib.audio.inspection import inspect_audio
from djlib.domain.errors import AppError


def inspect_catalog_audio(path: Path, inspector=inspect_audio):
    """Bind labels, complete-file hash and payload evidence to one stable file identity."""
    try:
        return _inspect(path, inspector)
    except (OSError, EOFError, wave.Error) as exc:
        raise AppError(
            "FILE_CHANGED", "The source became unavailable during catalog inspection."
        ) from exc


def _inspect(path, inspector):
    before = path_snapshot(path)
    if not before.is_regular or before.is_reparse:
        raise AppError("FILE_CHANGED", "Catalog inspection requires a regular, stable file.")
    inspection = inspector(path)
    if path.suffix.lower() == ".wav":
        # WAV-only installations need no FFmpeg. The format tuple prevents equal
        # raw sample bytes with different rate/channel interpretations matching.
        with wave.open(str(path), "rb") as reader:
            digest = hashlib.sha256()
            while chunk := reader.readframes(16_384):
                digest.update(chunk)
            payload = {
                "method": "wav_frames_v1",
                "sha256": digest.hexdigest(),
                "sample_width": reader.getsampwidth(),
                "sample_rate": reader.getframerate(),
                "channels": reader.getnchannels(),
                "frames": reader.getnframes(),
            }
    else:
        payload = {
            "method": "ffmpeg_pcm_s32le_v1",
            "sha256": pcm_hash(path),
            "sample_rate": inspection.sample_rate,
            "channels": inspection.channels,
        }
    after = path_snapshot(path)
    if before.signature != after.signature:
        raise AppError("FILE_CHANGED", "The source changed during catalog inspection.")
    return inspection, payload, after
