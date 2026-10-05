"""Exact decoded PCM comparison shared by catalog and delivery checks.

This detects changed samples; it does not identify a song or match different
masters. Callers compare stream properties separately and fence file changes.
"""

import re
import subprocess
from pathlib import Path

from djlib.domain.errors import AppError


def pcm_hash(path: Path) -> str:
    """A native tag edit may change file bytes, but must not change decoded audio."""
    try:
        reply = subprocess.run(
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
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s32le",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        value = reply.stdout.strip().removeprefix("SHA256=")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError
        return value
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise AppError(
            "AUDIO_VERIFY_FAILED", "FFmpeg could not verify the working-copy audio."
        ) from exc
