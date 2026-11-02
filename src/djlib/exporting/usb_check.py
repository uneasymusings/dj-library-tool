"""Read-only check that a crate's audio landed on a rekordbox USB export, byte for byte.

rekordbox copies each exported file into ``Contents/`` on the device. For every expected
track we look for files of the same size there and compare SHA-256, so a renamed copy still
counts and a same-named different file does not. Nothing on the device is written.
"""

import hashlib
import os
from collections import defaultdict
from pathlib import Path

from djlib.audio.file_identity import descriptor_snapshot, path_snapshot
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


def check_tracks(volume: Path, tracks: list[dict], *, on_progress=None) -> dict:
    """``tracks`` carry ``sha256`` and ``size_bytes``; returns found/missing counts and paths."""
    return _check_tracks(volume, tracks, _sha256, on_progress)


def _check_tracks(volume, tracks, hash_file, on_progress):
    contents = volume / "Contents"
    if not volume.is_dir():
        raise AppError("DEVICE_UNAVAILABLE", f"{volume.name} is not mounted; plug the USB back in.")
    if not contents.is_dir():
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
    if on_progress:
        on_progress(0, len(tracks))
    for position, track in enumerate(tracks, 1):
        match = None
        for candidate in by_size.get(int(track.get("size_bytes") or -1), []):
            if candidate not in hashes:
                try:
                    hashes[candidate] = hash_file(candidate)
                except OSError:
                    continue
            if hashes[candidate] == track["sha256"]:
                match = candidate
                break
        if match is None:
            missing.append(track.get("label") or track["sha256"][:12])
        else:
            found.append(str(match))
        if on_progress:
            on_progress(position, len(tracks))
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
    ready=None,
) -> bool:
    """Wait until rekordbox has finished writing the export; True if every file arrived.

    rekordbox shows its progress inside its own window, so completion is read from the
    stick: the device library changed, every expected file is present, ``ready()`` (if
    given) agrees — e.g. the library lists the playlist — and nothing changed for one more
    check. Files already on the stick from an earlier export are not enough on their own.
    If nothing changes for ``stall`` seconds the export is over anyway (for example a file
    was skipped) and False is returned.
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
        if updated and count == len(tracks) and (ready is None or ready()):
            return True
        if updated and clock() - changed_at >= stall:
            return False
    return False


def check_playlist(volume: Path, name: str, tracks: list[dict], *, on_progress=None) -> dict:
    """Check the playlist as a player sees it: the device library must list ``name`` with
    the expected tracks in order, each pointing at a file whose bytes match.

    Falls back to a content-only check of ``Contents/`` when the device library cannot be
    read; ``verified_by`` says which check ran.
    """
    return _check_playlist(volume, name, tracks, _sha256, on_progress)


def _check_playlist(volume, name, tracks, hash_file, on_progress):
    from djlib.exporting import rekordbox_pdb

    try:
        listed = rekordbox_pdb.playlist(volume, name)
    except AppError:
        listed = None
    if listed is None:
        result = _check_tracks(volume, tracks, hash_file, on_progress)
        return {
            **result,
            "playlist_on_device": False,
            "in_order": None,
            "verified_by": "usb_file_hashes",
        }
    entries = listed["tracks"]
    found, missing, analysis = 0, [], []
    if on_progress:
        on_progress(0, len(tracks))
    for position, track in enumerate(tracks):
        entry = entries[position] if position < len(entries) else {}
        path = volume / (entry.get("file_path") or "").lstrip("/")
        matches = False
        if entry.get("file_path") and path.is_file():
            try:
                matches = (
                    path.stat().st_size == int(track.get("size_bytes") or -1)
                    and hash_file(path) == track["sha256"]
                )
            except OSError:
                matches = False
        if matches:
            found += 1
            if entry.get("bpm") or entry.get("key"):
                analysis.append(
                    {"position": position, "bpm": entry.get("bpm"), "key": entry.get("key")}
                )
        else:
            missing.append(track.get("label") or track["sha256"][:12])
        if on_progress:
            on_progress(position + 1, len(tracks))
    return {
        "volume": str(volume),
        "expected": len(tracks),
        "found": found,
        "missing": missing[:20],
        "playlist_on_device": True,
        "device_entries": len(entries),
        "in_order": found == len(tracks) == len(entries),
        "same_name_playlists": listed["same_name_playlists"],
        "matched_by": "device_library_path_and_sha256",
        "verified_by": "device_library_and_file_hashes",
        "device_analysis": analysis,
        "device_written": False,
    }


def check_playlists(volume: Path, playlists: dict[str, list[dict]], *, on_progress=None) -> dict:
    """Check several playlists, reading shared audio once during this check only.

    Every playlist still checks exact entry order and file hashes. A changing library or
    file invalidates the entire check; cached hashes never survive into a later run.
    """
    total = sum(len(tracks) for tracks in playlists.values())
    if not total:
        raise AppError("INPUT_INVALID", "Pass at least one nonempty playlist to check.")
    before = library_state(volume)
    cache = {}

    def changed():
        return AppError(
            "DEVICE_CHANGED_DURING_CHECK",
            "The USB changed while it was being checked. Wait for export to finish, then retry.",
            retryable=True,
        )

    def signature(path):
        try:
            snapshot = path_snapshot(path)
        except OSError as exc:
            raise changed() from exc
        if not snapshot.is_regular or snapshot.is_reparse:
            raise changed()
        return snapshot

    def hash_file(path):
        snapshot = signature(path)
        if path in cache:
            previous, digest = cache[path]
            if previous.signature != snapshot.signature:
                raise changed()
            return digest
        digest = _sha256(path)
        if signature(path).signature != snapshot.signature:
            raise changed()
        cache[path] = (snapshot, digest)
        return digest

    def library_fingerprints():
        # FAT timestamps can be coarse: a same-size playlist edit can retain the
        # modification signature. Read these small database files independently of
        # the audio cache so database reads never count as checked audio files/bytes.
        state = library_state(volume)
        fingerprints = {}
        for name in state:
            path = volume / "PIONEER" / "rekordbox" / name
            snapshot = signature(path)
            try:
                with path.open("rb") as stream:
                    opened = descriptor_snapshot(stream.fileno())
                    if opened.signature != snapshot.signature:
                        raise changed()
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if descriptor_snapshot(stream.fileno()).signature != opened.signature:
                        raise changed()
                if signature(path).signature != opened.signature:
                    raise changed()
            except OSError as exc:
                raise changed() from exc
            fingerprints[name] = digest
        if library_state(volume) != state:
            raise changed()
        return fingerprints

    fingerprints = library_fingerprints()
    if library_state(volume) != before:
        raise changed()
    completed, results = 0, {}

    def progress(done, _):
        if on_progress:
            on_progress(completed + done, total)

    for name, tracks in playlists.items():
        results[name] = _check_playlist(volume, name, tracks, hash_file, progress)
        completed += len(tracks)
    if library_state(volume) != before:
        raise changed()
    if library_fingerprints() != fingerprints:
        raise changed()
    for path, (snapshot, _) in cache.items():
        if signature(path).signature != snapshot.signature:
            raise changed()
    return {
        "volume": str(volume),
        "playlists": results,
        "verified": all(
            result.get("playlist_on_device") is True
            and result.get("in_order") is True
            and result["found"] == result["expected"]
            for result in results.values()
        ),
        "unique_files_hashed": len(cache),
        "audio_bytes_read": sum(snapshot.size for snapshot, _ in cache.values()),
        "device_written": False,
        "player_playback_verified": False,
    }


def analysis_xml(destination: Path, analysis: list[dict], originals: list[str]) -> Path:
    """rekordbox's BPM/key for verified tracks as a Collection XML keyed by the originals.

    The device library holds rekordbox's own analysis of each exported track; writing it in
    rekordbox's interchange format lets the normal importer match it to catalog files.
    """
    from xml.etree import ElementTree

    root = ElementTree.Element("DJ_PLAYLISTS", Version="1.0.0")
    ElementTree.SubElement(root, "PRODUCT", Name="rekordbox", Version="device library")
    collection = ElementTree.SubElement(root, "COLLECTION", Entries=str(len(analysis)))
    for number, item in enumerate(analysis, 1):
        attributes = {
            "TrackID": str(number),
            "Location": Path(originals[item["position"]]).as_uri(),
        }
        if item.get("bpm"):
            attributes["AverageBpm"] = f"{item['bpm']:.2f}"
        if item.get("key"):
            attributes["Tonality"] = item["key"]
        ElementTree.SubElement(collection, "TRACK", attributes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(root).write(destination, encoding="UTF-8", xml_declaration=True)
    return destination
