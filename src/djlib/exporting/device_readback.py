"""Bounded, read-only evidence about a mounted volume, never native DJ readiness.

Database names are existence markers only: their contents are neither opened nor
interpreted. Optional SHA-256 readback proves bytes are present, not that a native
playlist refers to them, that analysis exists, or that a player can use them.
"""

import hashlib
import os
import platform
import plistlib
import re
import stat
import subprocess
import time
from pathlib import Path

from djlib.audio.inspection import SUPPORTED_EXTENSIONS
from djlib.domain.errors import AppError

MAX_AUDIO_FILES = 10_000
MAX_ENTRIES = 50_000
MAX_SCAN_BYTES = 64 * 1024**3
MAX_SCAN_SECONDS = 120
MAX_DEPTH = 32
DISKUTIL_TIMEOUT = 5
PARTITION_SCHEMES = frozenset(
    {"FDisk_partition_scheme", "GUID_partition_scheme", "Apple_partition_scheme"}
)
MARKERS = {
    "device_library": ("PIONEER/rekordbox/export.pdb",),
    "onelibrary": ("PIONEER/rekordbox/exportLibrary.db",),
    "serato": ("_Serato_", "_Serato_/Subcrates", "_Serato_/database V2"),
}


def _diskutil_info(value: str) -> dict:
    completed = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", value],
        capture_output=True,
        check=True,
        timeout=DISKUTIL_TIMEOUT,
    )
    data = plistlib.loads(completed.stdout)
    if not isinstance(data, dict):
        raise ValueError("diskutil did not return a dictionary")
    return data


def _partition_scheme(data: dict) -> str | None:
    value = data.get("PartitionMapScheme") or data.get("PartitionMapType")
    if value:
        return str(value)
    content = data.get("Content")
    return content if isinstance(content, str) and content in PARTITION_SCHEMES else None


def _volume_identity(path: Path) -> dict:
    info = path.stat()
    result = {
        "value": f"stat:{info.st_dev}:{info.st_ino}",
        "method": "stat_device_and_inode",
        "confidence": "weak",
        "stat_device": info.st_dev,
        "stat_inode": info.st_ino,
        "filesystem": None,
        "filesystem_type": None,
        "partition_scheme": None,
        "partition_scheme_source": None,
        "partition_type": None,
        "device_identifier": None,
        "parent_device_identifier": None,
    }
    if platform.system() != "Darwin":
        return result
    try:
        data = _diskutil_info(str(path))
        scheme = _partition_scheme(data)
        result.update(
            # "msdos" is a driver family, not proof of FAT16 versus FAT32.
            # Prefer the more specific personality/name and retain the raw type.
            filesystem=data.get("FileSystemPersonality")
            or data.get("FilesystemName")
            or data.get("FilesystemType")
            or data.get("TypeBundle"),
            filesystem_type=data.get("FilesystemType") or data.get("TypeBundle"),
            partition_scheme=scheme,
            partition_scheme_source="volume_diskutil_info" if scheme else None,
            partition_type=data.get("Content"),
            device_identifier=data.get("DeviceIdentifier"),
        )
        for key in ("VolumeUUID", "DiskUUID"):
            if data.get(key):
                result.update(
                    value=f"{key}:{data[key]}", method="diskutil_uuid", confidence="strong"
                )
                break
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException):
        result["warning"] = "diskutil identity unavailable; stat identity is weak and reusable"
        return result
    # Mounted partition metadata commonly omits the partition map. A single
    # bounded query of its declared whole disk can provide that fact. Never
    # invent a parent identifier or recurse through arbitrary diskutil output.
    parent = data.get("ParentWholeDisk") or data.get("PartOfWhole")
    if isinstance(parent, str) and re.fullmatch(r"(?:/dev/)?disk\d+", parent):
        result["parent_device_identifier"] = parent.removeprefix("/dev/")
        if not result["partition_scheme"]:
            try:
                parent_data = _diskutil_info("/dev/" + result["parent_device_identifier"])
                scheme = _partition_scheme(parent_data)
                result.update(
                    partition_scheme=scheme,
                    partition_scheme_source="parent_diskutil_info" if scheme else None,
                )
            except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException):
                result["partition_warning"] = "Parent disk partition scheme could not be read"
    return result


def _markers(path: Path) -> dict:
    result = {}
    for kind, names in MARKERS.items():
        found = []
        for name in names:
            candidate = path
            try:
                for component in Path(name).parts:
                    candidate = candidate / component
                    mode = candidate.lstat().st_mode
                    if stat.S_ISLNK(mode):
                        break
                else:
                    directory_marker = name in {"_Serato_", "_Serato_/Subcrates"}
                    if (directory_marker and stat.S_ISDIR(mode)) or (
                        not directory_marker and stat.S_ISREG(mode)
                    ):
                        found.append(name)
            except OSError:
                pass
        result[kind] = {
            "present": bool(found),
            "paths": found,
            "evidence": "existence_only",
            "freshness": "unknown",
            "contents_verified": False,
        }
    return result


class _ScanLimit(Exception):
    pass


def _hash_inventory(path: Path, expected: list[str], device: int) -> dict:
    result = {
        "requested": expected,
        "matched": [],
        "missing": list(expected),
        "matches": [],
        "files_hashed": 0,
        "files_examined": 0,
        "entries_examined": 0,
        "bytes_read": 0,
        "symlinks_skipped": 0,
        "other_devices_skipped": 0,
        "complete": False,
        "traversal_complete": False,
        "errors": [],
        "limits": {
            "audio_files": MAX_AUDIO_FILES,
            "entries": MAX_ENTRIES,
            "bytes": MAX_SCAN_BYTES,
            "seconds": MAX_SCAN_SECONDS,
            "depth": MAX_DEPTH,
        },
        "scope": "supported_audio_on_this_volume; no symlinks or nested volumes",
        "verification_method": "sha256_presence_only",
    }
    if not expected:
        result["scan_performed"] = False
        return result
    result["scan_performed"] = True
    # A resolve-then-open fallback has a symlink race. Fail closed on platforms
    # without descriptor-relative no-follow traversal instead of claiming safety.
    if (
        not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
    ):
        result["errors"].append({"code": "SAFE_READBACK_UNSUPPORTED"})
        return result
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    found: dict[str, list[str]] = {}
    cache: dict[tuple, str] = {}
    wanted = set(expected)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_flags = flags | os.O_DIRECTORY

    def budget():
        if time.monotonic() > deadline:
            raise _ScanLimit("SCAN_TIME_LIMIT")

    def walk(directory_fd: int, relative: Path, depth: int):
        budget()
        if depth > MAX_DEPTH:
            raise _ScanLimit("SCAN_DEPTH_LIMIT")
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                budget()
                result["entries_examined"] += 1
                if result["entries_examined"] > MAX_ENTRIES:
                    raise _ScanLimit("SCAN_ENTRY_LIMIT")
                rel = relative / entry.name
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        result["symlinks_skipped"] += 1
                        continue
                    if info.st_dev != device:
                        result["other_devices_skipped"] += 1
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = os.open(entry.name, directory_flags, dir_fd=directory_fd)
                        try:
                            if os.fstat(child_fd).st_dev == device:
                                walk(child_fd, rel, depth + 1)
                        finally:
                            os.close(child_fd)
                    elif (
                        stat.S_ISREG(info.st_mode)
                        and rel.suffix.lower() in SUPPORTED_EXTENSIONS
                        and not entry.name.startswith("._")
                    ):
                        result["files_examined"] += 1
                        if result["files_examined"] > MAX_AUDIO_FILES:
                            raise _ScanLimit("SCAN_FILE_LIMIT")
                        file_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                        with os.fdopen(file_fd, "rb") as stream:
                            before = os.fstat(stream.fileno())
                            if not stat.S_ISREG(before.st_mode) or before.st_dev != device:
                                raise OSError("File changed before readback")
                            identity = (
                                before.st_dev,
                                before.st_ino,
                                before.st_size,
                                before.st_mtime_ns,
                                before.st_ctime_ns,
                            )
                            digest = cache.get(identity)
                            if digest is None:
                                if before.st_size > MAX_SCAN_BYTES - result["bytes_read"]:
                                    raise _ScanLimit("SCAN_BYTE_LIMIT")
                                hasher = hashlib.sha256()
                                while chunk := stream.read(1024 * 1024):
                                    budget()
                                    result["bytes_read"] += len(chunk)
                                    if result["bytes_read"] > MAX_SCAN_BYTES:
                                        raise _ScanLimit("SCAN_BYTE_LIMIT")
                                    hasher.update(chunk)
                                after = os.fstat(stream.fileno())
                                if identity != (
                                    after.st_dev,
                                    after.st_ino,
                                    after.st_size,
                                    after.st_mtime_ns,
                                    after.st_ctime_ns,
                                ):
                                    raise OSError("File changed during readback")
                                digest = hasher.hexdigest()
                                cache[identity] = digest
                                result["files_hashed"] += 1
                        if digest in wanted:
                            found.setdefault(digest, []).append(rel.as_posix())
                    if wanted <= found.keys():
                        return
                except OSError as exc:
                    result["errors"].append(
                        {"code": "READ_FAILED", "path": rel.as_posix(), "message": str(exc)}
                    )

    try:
        root_fd = os.open(path, directory_flags)
        try:
            if os.fstat(root_fd).st_dev != device:
                raise OSError("Volume changed before readback")
            walk(root_fd, Path(), 0)
        finally:
            os.close(root_fd)
        result["traversal_complete"] = not wanted <= found.keys()
        result["complete"] = not result["errors"]
    except _ScanLimit as exc:
        result["errors"].append({"code": str(exc)})
    except OSError as exc:
        result["errors"].append({"code": "READ_FAILED", "message": str(exc)})
    result["matched"] = [value for value in expected if value in found]
    result["missing"] = [value for value in expected if value not in found]
    result["matches"] = [
        {"sha256": value, "relative_paths": found[value]} for value in result["matched"]
    ]
    return result


def inspect_device(path: str, expected_hashes: list[str] | None = None) -> dict:
    """Inspect a mounted root; hash audio only when expected SHA-256 IDs are supplied.

    Ordinary directories return ``not_mounted`` without scanning. Hash matching is
    bounded and may stop as soon as every requested ID is found. Missing IDs after
    an incomplete scan are unverified, not proof that the recording is absent.
    """
    values = expected_hashes if expected_hashes is not None else []
    if (
        not isinstance(values, list)
        or len(values) > MAX_AUDIO_FILES
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value)
            for value in values
        )
    ):
        raise AppError("EXPECTED_HASH_INVALID", "Supply at most 10000 SHA-256 hexadecimal IDs.")
    expected = list(dict.fromkeys(value.lower() for value in values))
    try:
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        before = _volume_identity(root)
    except (OSError, ValueError) as exc:
        raise AppError("DEVICE_UNAVAILABLE", "Choose an existing mounted volume root.") from exc
    mounted = root.is_mount()
    result = {
        "path": str(root),
        "is_mount_point": mounted,
        "status": "inventory_only" if mounted else "not_mounted",
        "volume_identity": before,
        "native_markers": _markers(root) if mounted else {},
        "hash_inventory": None,
        "volume_unchanged": None,
        "writes_performed": False,
        "native_export_verified": False,
        "hardware_verified": False,
        "player_compatibility": "unknown",
        "readiness_confidence": "unknown",
        "device_state": "not_verified",
    }
    if not mounted:
        return result
    inventory = _hash_inventory(root, expected, before["stat_device"])
    result["hash_inventory"] = inventory
    identity_unverified = False
    try:
        after = _volume_identity(root)
        same_mount = root.is_mount() and all(
            before[key] == after[key] for key in ("stat_device", "stat_inode")
        )
        identity_unverified = same_mount and before["method"] != after["method"]
        unchanged = (
            None if identity_unverified else same_mount and before["value"] == after["value"]
        )
    except OSError:
        after, unchanged = None, False
    result.update(volume_identity_after=after, volume_unchanged=unchanged)
    if identity_unverified:
        result["status"] = "identity_unverified"
        inventory.update(complete=False, matched=[], missing=expected, matches=[])
        inventory["errors"].append({"code": "VOLUME_IDENTITY_UNVERIFIED"})
    elif not unchanged:
        result["status"] = "volume_changed"
        inventory.update(complete=False, matched=[], missing=expected, matches=[])
        inventory["errors"].append({"code": "VOLUME_CHANGED"})
    elif expected:
        result["status"] = (
            "incomplete"
            if not inventory["complete"]
            else "missing_hashes"
            if inventory["missing"]
            else "verified_hashes"
        )
    return result
