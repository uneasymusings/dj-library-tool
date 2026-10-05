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
import struct
import subprocess
import time
from pathlib import Path

from djlib.audio.file_identity import descriptor_snapshot, handle_snapshot, path_snapshot
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
_WINDOWS = os.name == "nt"


class _WindowsReadback:
    """Read-only native handles; child paths are always relative to a held parent.

    NtCreateFile's FILE_OPEN_REPARSE_POINT opens the link itself. Directory
    enumeration uses NtQueryDirectoryFile on the handle, never a path that can
    be replaced between checks. No write, creation, or delete access is requested.
    Microsoft contracts: winternl/NtCreateFile, ntifs/NtQueryDirectoryFile.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll")

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.USHORT),
                ("maximum", wintypes.USHORT),
                ("buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.ULONG),
                ("root", wintypes.HANDLE),
                ("name", ctypes.POINTER(UnicodeString)),
                ("attributes", wintypes.ULONG),
                ("security", ctypes.c_void_p),
                ("quality", ctypes.c_void_p),
            ]

        class StatusBlock(ctypes.Structure):
            # The first member is a union of NTSTATUS and a pointer; pointer size
            # also supplies the documented alignment on both 32- and 64-bit hosts.
            _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

        self.UnicodeString, self.ObjectAttributes, self.StatusBlock = (
            UnicodeString,
            ObjectAttributes,
            StatusBlock,
        )
        self.kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.nt.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(StatusBlock),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        ]
        self.nt.NtCreateFile.restype = ctypes.c_long
        self.nt.NtQueryDirectoryFile.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(StatusBlock),
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        self.nt.NtQueryDirectoryFile.restype = ctypes.c_long
        self.nt.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
        self.nt.RtlNtStatusToDosError.restype = wintypes.ULONG

    def close(self, handle):
        self.kernel.CloseHandle(handle)

    def open_root(self, path):
        # OPEN_EXISTING; BACKUP_SEMANTICS allows directory handles. Sharing
        # permits writes to be detected, but prevents deletion of held paths.
        handle = self.kernel.CreateFileW(
            str(path), 0x80000000, 0x3, None, 3, 0x02000000 | 0x00200000, None
        )
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return handle

    def open_child(self, parent, name, directory, *, lock_writes=False):
        from ctypes import wintypes

        if not name or name in {".", ".."} or any(c in name for c in "\\/:\x00"):
            raise OSError("Unsafe directory entry name")
        c = self.ctypes
        length = len(name.encode("utf-16-le"))
        buffer = c.create_unicode_buffer(name, length // 2 + 1)
        text = self.UnicodeString(length, length + 2, c.cast(buffer, wintypes.LPWSTR))
        attrs = self.ObjectAttributes(
            c.sizeof(self.ObjectAttributes), parent, c.pointer(text), 0x40, None, None
        )
        status, handle = self.StatusBlock(), wintypes.HANDLE()
        # FILE_LIST_DIRECTORY or FILE_READ_DATA (same bit), READ_ATTRIBUTES,
        # SYNCHRONIZE; FILE_OPEN, no-follow, synchronous, required object type.
        code = self.nt.NtCreateFile(
            c.byref(handle),
            0x100081,
            c.byref(attrs),
            c.byref(status),
            None,
            0,
            0x1 if lock_writes else 0x3,
            1,
            0x00200000 | 0x20 | (0x1 if directory else 0x40),
            None,
            0,
        )
        if code < 0:
            raise c.WinError(self.nt.RtlNtStatusToDosError(code))
        return handle.value

    def entries(self, handle):
        c = self.ctypes
        buffer, status = c.create_string_buffer(64 * 1024), self.StatusBlock()
        restart = True
        while True:
            code = self.nt.NtQueryDirectoryFile(
                handle,
                None,
                None,
                None,
                c.byref(status),
                buffer,
                len(buffer),
                1,
                False,
                None,
                restart,
            )
            restart = False
            if code & 0xFFFFFFFF == 0x80000006:  # STATUS_NO_MORE_FILES
                return
            if code < 0:
                raise c.WinError(self.nt.RtlNtStatusToDosError(code))
            if not status.information or status.information > len(buffer):
                raise OSError("Invalid directory enumeration length")
            yield from _windows_directory_entries(buffer.raw[: status.information])


def _windows_directory_entries(data):
    """Decode bounded FILE_DIRECTORY_INFORMATION records (names, never file data)."""
    offset = 0
    while True:
        if len(data) - offset < 64:
            raise OSError("Incomplete directory record")
        next_entry = struct.unpack_from("<I", data, offset)[0]
        attributes, name_length = struct.unpack_from("<II", data, offset + 56)
        end = offset + 64 + name_length
        if name_length % 2 or end > len(data):
            raise OSError("Invalid directory name length")
        try:
            name = data[offset + 64 : end].decode("utf-16-le")
        except UnicodeError as exc:
            raise OSError("Invalid directory name encoding") from exc
        if name not in {".", ".."}:
            if not name or any(c in name for c in "\\/:\x00"):
                raise OSError("Unsafe directory entry name")
            yield name, attributes
        if not next_entry:
            return
        if next_entry < 64 + name_length or next_entry % 8 or offset + next_entry >= len(data):
            raise OSError("Invalid directory record offset")
        offset += next_entry


def _windows_volume_info(path):
    """Query the mount manager's volume GUID; serial numbers alone are weak IDs."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumeNameForVolumeMountPointW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    kernel.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    root = str(path).rstrip("\\/") + "\\"
    guid, filesystem = ctypes.create_unicode_buffer(128), ctypes.create_unicode_buffer(128)
    if not kernel.GetVolumeNameForVolumeMountPointW(root, guid, len(guid)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetVolumeInformationW(
        root, None, 0, None, None, None, filesystem, len(filesystem)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "value": guid.value,
        "method": "windows_volume_guid",
        "confidence": "strong",
        "filesystem": filesystem.value,
        "filesystem_type": filesystem.value,
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
    if platform.system() == "Windows":
        try:
            result.update(_windows_volume_info(path))
        except OSError:
            result["warning"] = (
                "Windows volume GUID unavailable; stat identity is weak and reusable"
            )
        return result
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
    if _WINDOWS:
        return _windows_markers(path)
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


def _windows_markers(path):
    result = {
        kind: {
            "present": False,
            "paths": [],
            "evidence": "existence_only",
            "freshness": "unknown",
            "contents_verified": False,
        }
        for kind in MARKERS
    }
    api, root = _WindowsReadback(), None
    try:
        root = api.open_root(path)
        root_info = handle_snapshot(root)
        if root_info.is_reparse or root_info.is_regular:
            return result
        for kind, names in MARKERS.items():
            for name in names:
                handles = []
                try:
                    parent = root
                    components = Path(name).parts
                    for index, component in enumerate(components):
                        directory = index < len(components) - 1 or name in {
                            "_Serato_",
                            "_Serato_/Subcrates",
                        }
                        child = api.open_child(parent, component, directory)
                        handles.append(child)
                        info = handle_snapshot(child)
                        if info.is_reparse or info.identity[0] != root_info.identity[0]:
                            break
                        parent = child
                    else:
                        result[kind]["paths"].append(name)
                except OSError:
                    pass
                finally:
                    for handle in reversed(handles):
                        api.close(handle)
            result[kind]["present"] = bool(result[kind]["paths"])
    except OSError:
        pass
    finally:
        if root is not None:
            api.close(root)
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
    if _WINDOWS:
        return _windows_hash_inventory(path, expected, device, result)
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


def _windows_hash_inventory(path, expected, device, result):
    import msvcrt

    api, root = _WindowsReadback(), None
    deadline, found, cache, wanted = time.monotonic() + MAX_SCAN_SECONDS, {}, {}, set(expected)

    def budget():
        if time.monotonic() > deadline:
            raise _ScanLimit("SCAN_TIME_LIMIT")

    def walk(parent, relative, depth, volume):
        budget()
        if depth > MAX_DEPTH:
            raise _ScanLimit("SCAN_DEPTH_LIMIT")
        for name, attributes in api.entries(parent):
            budget()
            result["entries_examined"] += 1
            if result["entries_examined"] > MAX_ENTRIES:
                raise _ScanLimit("SCAN_ENTRY_LIMIT")
            rel = relative / name
            if attributes & 0x400:  # Every reparse point, including directory junctions.
                result["symlinks_skipped"] += 1
                continue
            directory = bool(attributes & 0x10)
            if not directory and (
                rel.suffix.lower() not in SUPPORTED_EXTENSIONS or name.startswith("._")
            ):
                continue
            child = None
            try:
                # A file being hashed denies concurrent writers as well as
                # rename/delete. This also protects coarse FAT timestamps.
                child = api.open_child(parent, name, directory, lock_writes=not directory)
                before = handle_snapshot(child)
                # Recheck the object actually opened: the directory entry may
                # have changed into a reparse point since enumeration.
                if before.is_reparse:
                    result["symlinks_skipped"] += 1
                    continue
                if before.identity[0] != volume:
                    result["other_devices_skipped"] += 1
                    continue
                if directory:
                    if before.is_regular:
                        raise OSError("Directory changed before readback")
                    walk(child, rel, depth + 1, volume)
                else:
                    if not before.is_regular:
                        raise OSError("Not a regular disk file")
                    result["files_examined"] += 1
                    if result["files_examined"] > MAX_AUDIO_FILES:
                        raise _ScanLimit("SCAN_FILE_LIMIT")
                    value = cache.get(before.signature)
                    if value is None:
                        if before.size > MAX_SCAN_BYTES - result["bytes_read"]:
                            raise _ScanLimit("SCAN_BYTE_LIMIT")
                        descriptor = msvcrt.open_osfhandle(child, os.O_RDONLY | os.O_BINARY)
                        child = None  # The descriptor now owns this handle.
                        try:
                            stream = os.fdopen(descriptor, "rb")
                        except BaseException:
                            os.close(descriptor)
                            raise
                        with stream:
                            hasher = hashlib.sha256()
                            while chunk := stream.read(1024 * 1024):
                                budget()
                                result["bytes_read"] += len(chunk)
                                if result["bytes_read"] > MAX_SCAN_BYTES:
                                    raise _ScanLimit("SCAN_BYTE_LIMIT")
                                hasher.update(chunk)
                            if descriptor_snapshot(stream.fileno()).signature != before.signature:
                                raise OSError("File changed during readback")
                        value = hasher.hexdigest()
                        cache[before.signature] = value
                        result["files_hashed"] += 1
                    if value in wanted:
                        found.setdefault(value, []).append(rel.as_posix())
                if wanted <= found.keys():
                    return
            except OSError as exc:
                result["errors"].append(
                    {"code": "READ_FAILED", "path": rel.as_posix(), "message": str(exc)}
                )
            finally:
                if child is not None:
                    api.close(child)

    try:
        before_root = path_snapshot(path)
        if path.stat().st_dev != device or before_root.is_regular or before_root.is_reparse:
            raise OSError("Volume changed before readback")
        root = api.open_root(path)
        root_info = handle_snapshot(root)
        if root_info.identity != before_root.identity or root_info.is_reparse:
            raise OSError("Volume changed before readback")
        walk(root, Path(), 0, root_info.identity[0])
        result["traversal_complete"] = not wanted <= found.keys()
        result["complete"] = not result["errors"]
    except _ScanLimit as exc:
        result["errors"].append({"code": str(exc)})
    except OSError as exc:
        result["errors"].append({"code": "READ_FAILED", "message": str(exc)})
    finally:
        if root is not None:
            api.close(root)
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
