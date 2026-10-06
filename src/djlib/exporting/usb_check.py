"""Read-only check that a crate's audio landed on a rekordbox USB export, byte for byte.

rekordbox copies each exported file into ``Contents/`` on the device. For every expected
track we look for files of the same size there and compare SHA-256, so a renamed copy still
counts and a same-named different file does not. Nothing on the device is written.
"""

import hashlib
import os
from collections import defaultdict
from pathlib import Path

from djlib.domain.errors import AppError

AUDIO = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".mp4", ".aac", ".ogg", ".alac"}


def library_state(volume: Path) -> dict:
    """Modification signature of the device library rekordbox maintains on the volume."""
    folder = volume / "PIONEER" / "rekordbox"
    state = {}
    for name in ("export.pdb", "exportLibrary.db", "exportLibrary.db-wal"):
        try:
            stat = (folder / name).stat()
        except OSError:
            continue
        state[name] = [stat.st_mtime_ns, stat.st_size]
    return state


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_tracks(volume: Path, tracks: list[dict]) -> dict:
    """``tracks`` carry ``sha256`` and ``size_bytes``; returns found/missing counts and paths."""
    contents = volume / "Contents"
    if not volume.is_dir() or not contents.is_dir():
        raise AppError("DEVICE_UNAVAILABLE", "The USB has no rekordbox Contents folder yet.")
    by_size: dict[int, list[Path]] = defaultdict(list)
    for root, _, files in os.walk(contents):
        for name in files:
            path = Path(root) / name
            if name.startswith("._") or path.suffix.lower() not in AUDIO:
                continue
            try:
                by_size[path.stat().st_size].append(path)
            except OSError:
                continue
    hashes: dict[Path, str] = {}
    found, missing = [], []
    for track in tracks:
        match = None
        for candidate in by_size.get(int(track.get("size_bytes") or -1), []):
            if candidate not in hashes:
                try:
                    hashes[candidate] = _sha256(candidate)
                except OSError:
                    continue
            if hashes[candidate] == track["sha256"]:
                match = candidate
                break
        if match is None:
            missing.append(track.get("label") or track["sha256"][:12])
        else:
            found.append(str(match))
    return {
        "volume": str(volume),
        "expected": len(tracks),
        "found": len(found),
        "missing": missing[:20],
        "matched_by": "size_and_sha256",
        "device_written": False,
    }


def copy_progress(volume: Path, tracks: list[dict]) -> tuple[int, tuple]:
    """How many expected file sizes are present under ``Contents/`` (cheap, no hashing),
    plus a signature of the stick that changes while rekordbox is still writing."""
    sizes = []
    for root, _, files in os.walk(volume / "Contents"):
        for name in files:
            if name.startswith("._"):
                continue
            try:
                sizes.append((Path(root) / name).stat().st_size)
            except OSError:
                continue
    present = set(sizes)
    count = sum(1 for track in tracks if int(track.get("size_bytes") or -1) in present)
    return count, (len(sizes), sum(sizes), sorted(library_state(volume).items()))


def wait_for_copy(
    volume: Path,
    tracks: list[dict],
    before: dict,
    on_progress=None,
    interval: float = 3.0,
    stall: float = 90.0,
    timeout: float = 3600.0,
    clock=None,
    sleep=None,
) -> bool:
    """Wait until rekordbox has finished writing the export; True if every file arrived.

    rekordbox shows its progress inside its own window, so completion is read from the
    stick: the device library changed, every expected file is present and nothing changed
    for one more check. If nothing changes for ``stall`` seconds the export is over anyway
    (for example a file was skipped) and False is returned.
    """
    import time

    clock, sleep = clock or time.monotonic, sleep or time.sleep
    started = changed_at = clock()
    last = None
    while clock() - started < timeout:
        sleep(interval)
        count, signature = copy_progress(volume, tracks)
        if on_progress is not None:
            on_progress(count)
        updated = library_state(volume) != before
        if signature != last:
            last, changed_at = signature, clock()
            continue
        if updated and count == len(tracks):
            return True
        if updated and clock() - changed_at >= stall:
            return False
    return False
