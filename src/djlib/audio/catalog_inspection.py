"""Stable catalog inspections and exact decoded-payload comparison, not recognition."""

import dataclasses
import hashlib
import wave
from pathlib import Path

from djlib.audio.file_identity import path_snapshot
from djlib.audio.fingerprint import pcm_hash
from djlib.audio.inspection import Inspection, checksum, inspect_audio
from djlib.domain.errors import AppError


def inspect_catalog_audio(path: Path, inspector=inspect_audio, known=None):
    """Bind labels, complete-file hash and payload evidence to one stable file identity.

    ``known(sha256)`` may return stored properties for bytes already in the catalog; those
    identical bytes are not decoded again (their decode result cannot differ).
    """
    try:
        return _inspect(path, inspector, known)
    except (OSError, EOFError, wave.Error) as exc:
        raise AppError(
            "FILE_CHANGED", "The source became unavailable during catalog inspection."
        ) from exc


def _inspect(path, inspector, known=None):
    before = path_snapshot(path)
    if not before.is_regular or before.is_reparse:
        raise AppError("FILE_CHANGED", "Catalog inspection requires a regular, stable file.")
    if known is not None:
        stored = known(checksum(path))
        fields = {field.name for field in dataclasses.fields(Inspection)}
        if stored and stored.get("audio_payload") and fields <= stored.keys():
            after = path_snapshot(path)
            if before.signature != after.signature:
                raise AppError("FILE_CHANGED", "The source changed during catalog inspection.")
            inspection = Inspection(**{field: stored[field] for field in fields})
            return inspection, dict(stored["audio_payload"]), after
    pcm = {} if inspector is inspect_audio else None
    inspection = inspector(path, pcm) if pcm is not None else inspector(path)
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
            "sha256": (pcm or {}).get("sha256") or pcm_hash(path),
            "sample_rate": inspection.sample_rate,
            "channels": inspection.channels,
        }
    after = path_snapshot(path)
    if before.signature != after.signature:
        raise AppError("FILE_CHANGED", "The source changed during catalog inspection.")
    return inspection, payload, after
