"""Read rekordbox's own analysis files (ANLZ0000.DAT) without opening rekordbox.

rekordbox writes one analysis folder per track under ``share/PIONEER/USBANLZ``. The DAT file
holds the track's file name (PPTH), its beat grid (PQTZ, tempo per beat) and cue lists
(PCOB). These files are read-only here; rekordbox's encrypted database is never touched,
so musical key (stored only in that database) still comes from an XML export.
Format reference: https://djl-analysis.deepsymmetry.org/djl-analysis/anlz.html
"""

import os
import statistics
import struct
import sys
import unicodedata
from pathlib import Path

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TAGS = 64
MAX_BEATS = 200_000


def default_root() -> Path | None:
    """rekordbox's analysis folder for this user, when rekordbox is installed."""
    override = os.environ.get("DJLIB_REKORDBOX_ANLZ")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library/Pioneer/rekordbox/share/PIONEER/USBANLZ"
    elif os.name == "nt" and os.environ.get("APPDATA"):
        root = Path(os.environ["APPDATA"]) / "Pioneer/rekordbox/share/PIONEER/USBANLZ"
    else:
        return None
    return root if root.is_dir() else None


def analysis_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/ANLZ0000.DAT"))


def parse(path: Path) -> dict | None:
    """File name, BPM and cue counts from one DAT file; None when it is not readable."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"PMAI":
        return None
    result = {"name": None, "bpm": None, "beats": 0, "hot_cues": 0, "memory_cues": 0}
    offset = struct.unpack(">I", data[4:8])[0]
    for _ in range(MAX_TAGS):
        if offset + 12 > len(data):
            break
        fourcc = data[offset : offset + 4]
        header, length = struct.unpack(">II", data[offset + 4 : offset + 12])
        if length < 12 or offset + length > len(data):
            break
        body = data[offset : offset + length]
        try:
            if fourcc == b"PPTH" and len(body) >= 16:
                size = struct.unpack(">I", body[12:16])[0]
                text = body[16 : 16 + size].decode("utf-16-be").rstrip("\x00")
                result["name"] = unicodedata.normalize(
                    "NFC", text.replace("\\", "/").rsplit("/", 1)[-1]
                )
            elif fourcc == b"PQTZ" and len(body) >= 24:
                count = min(struct.unpack(">I", body[20:24])[0], MAX_BEATS)
                tempos = [
                    struct.unpack(">H", body[header + 8 * i + 2 : header + 8 * i + 4])[0] / 100
                    for i in range(count)
                    if header + 8 * i + 4 <= len(body)
                ]
                tempos = [tempo for tempo in tempos if 20 <= tempo <= 400]
                result["beats"] = len(tempos)
                result["bpm"] = round(statistics.median(tempos), 2) if tempos else None
            elif fourcc == b"PCOB" and len(body) >= 20:
                kind = struct.unpack(">I", body[12:16])[0]
                cues = struct.unpack(">H", body[18:20])[0]
                result["hot_cues" if kind == 1 else "memory_cues"] += cues
        except (struct.error, UnicodeDecodeError, statistics.StatisticsError):
            continue
        offset += length
    return result if result["name"] else None
