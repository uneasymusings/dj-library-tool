"""Comparable path/descriptor observations without Windows CRT stat assumptions.

Windows path and descriptor stat timestamps have differed between Python versions.
Use the same native handle queries for both. A path observation opens metadata only,
without following its final reparse point; it never reads file contents.
"""

import ctypes
import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_WINDOWS = os.name == "nt"
_REPARSE_POINT = 0x400
_DIRECTORY = 0x10


@dataclass(frozen=True)
class FileSnapshot:
    identity: tuple
    size: int
    modified: int
    changed: int
    is_regular: bool
    is_reparse: bool

    @property
    def signature(self):
        return (
            self.identity,
            self.size,
            self.modified,
            self.changed,
            self.is_regular,
            self.is_reparse,
        )


def _stat_snapshot(value):
    return FileSnapshot(
        (value.st_dev, value.st_ino),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        stat.S_ISREG(value.st_mode),
        stat.S_ISLNK(value.st_mode)
        or bool(getattr(value, "st_file_attributes", 0) & _REPARSE_POINT),
    )


class _BasicInfo(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_int64),
        ("LastAccessTime", ctypes.c_int64),
        ("LastWriteTime", ctypes.c_int64),
        ("ChangeTime", ctypes.c_int64),
        ("FileAttributes", ctypes.c_uint32),
    ]


class _StandardInfo(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_int64),
        ("EndOfFile", ctypes.c_int64),
        ("NumberOfLinks", ctypes.c_uint32),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class _IdInfo(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_uint64),
        ("FileId", ctypes.c_ubyte * 16),
    ]


class _LegacyInfo(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", ctypes.c_uint32),
        ("CreationTime", ctypes.c_uint32 * 2),
        ("LastAccessTime", ctypes.c_uint32 * 2),
        ("LastWriteTime", ctypes.c_uint32 * 2),
        ("VolumeSerialNumber", ctypes.c_uint32),
        ("FileSizeHigh", ctypes.c_uint32),
        ("FileSizeLow", ctypes.c_uint32),
        ("NumberOfLinks", ctypes.c_uint32),
        ("FileIndexHigh", ctypes.c_uint32),
        ("FileIndexLow", ctypes.c_uint32),
    ]


@lru_cache(maxsize=1)
def _windows_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandleEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel.GetFileType.argtypes = [ctypes.c_void_p]
    kernel.GetFileType.restype = ctypes.c_uint32
    kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetFileInformationByHandle.restype = ctypes.c_int
    kernel.GetVolumeInformationByHandleW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    ]
    kernel.GetVolumeInformationByHandleW.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    return kernel


def _windows_error():
    return ctypes.WinError(ctypes.get_last_error())


def _legacy_identity(kernel, handle):
    # FAT/exFAT may lack FileIdInfo. Microsoft documents the legacy file index
    # plus volume serial for these filesystems, but warns it is insufficient on
    # ReFS. Never fall back on an unknown filesystem or to size/timestamps alone.
    filesystem = ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumeInformationByHandleW(
        handle, None, 0, None, None, None, filesystem, len(filesystem)
    ):
        raise _windows_error()
    if filesystem.value.casefold() not in {"fat", "fat32", "exfat", "ntfs"}:
        raise OSError("The filesystem requires a supported 128-bit file identity")
    legacy = _LegacyInfo()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(legacy)):
        raise _windows_error()
    index = (legacy.FileIndexHigh << 32) | legacy.FileIndexLow
    return legacy.VolumeSerialNumber, index.to_bytes(16, "little")


def handle_snapshot(handle: int) -> FileSnapshot:
    """Observe a Windows handle without reading it or taking ownership of it."""
    kernel = _windows_api()
    if kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK; never read pipes/devices.
        raise OSError("File identity requires a disk file handle")
    basic, standard, identity = _BasicInfo(), _StandardInfo(), _IdInfo()
    for info_class, value in ((0, basic), (1, standard)):
        if not kernel.GetFileInformationByHandleEx(
            handle, info_class, ctypes.byref(value), ctypes.sizeof(value)
        ):
            raise _windows_error()
    if kernel.GetFileInformationByHandleEx(
        handle, 18, ctypes.byref(identity), ctypes.sizeof(identity)
    ):
        volume, file_id = identity.VolumeSerialNumber, bytes(identity.FileId)
    else:
        volume, file_id = _legacy_identity(kernel, handle)
    if not any(file_id):
        raise OSError("The filesystem did not supply a stable file identity")
    return FileSnapshot(
        (volume, file_id),
        standard.EndOfFile,
        basic.LastWriteTime,
        basic.ChangeTime,
        not (standard.Directory or basic.FileAttributes & _DIRECTORY),
        bool(basic.FileAttributes & _REPARSE_POINT),
    )


def path_snapshot(path: Path) -> FileSnapshot:
    """Observe a path itself, rejecting no identity fields in favor of weak timestamps."""
    path = Path(path)
    value = path.lstat()
    preliminary = _stat_snapshot(value)
    if (
        not _WINDOWS
        or preliminary.is_reparse
        or not (preliminary.is_regular or stat.S_ISDIR(value.st_mode))
    ):
        return preliminary
    kernel = _windows_api()
    # FILE_READ_ATTRIBUTES, shared read/write/delete, OPEN_EXISTING,
    # OPEN_REPARSE_POINT | BACKUP_SEMANTICS. No content access is requested.
    handle = kernel.CreateFileW(str(path.absolute()), 0x80, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise _windows_error()
    try:
        return handle_snapshot(handle)
    finally:
        kernel.CloseHandle(handle)


def descriptor_snapshot(descriptor: int) -> FileSnapshot:
    """Observe the already-open descriptor; on Windows use its actual native handle."""
    if not _WINDOWS:
        return _stat_snapshot(os.fstat(descriptor))
    import msvcrt

    return handle_snapshot(msvcrt.get_osfhandle(descriptor))
