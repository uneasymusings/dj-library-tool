"""Real local identities plus portable coverage of the Windows native API boundary."""

import ctypes
import os
import sys
from types import SimpleNamespace

import pytest

from djlib.audio import file_identity as identity


def test_real_path_and_open_descriptor_have_the_same_stable_identity(tmp_path):
    path = tmp_path / "original café.bin"
    path.write_bytes(b"original\r\nbytes\x00\xff")
    before = identity.path_snapshot(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        opened = identity.descriptor_snapshot(descriptor)
        assert os.read(descriptor, 100) == path.read_bytes()
        assert identity.descriptor_snapshot(descriptor).signature == opened.signature
    finally:
        os.close(descriptor)
    assert before.is_regular and not before.is_reparse
    assert before.identity[1]
    assert before.signature == opened.signature == identity.path_snapshot(path).signature


def test_identical_replacement_bytes_and_mtime_do_not_replace_file_identity(tmp_path):
    path, replacement = tmp_path / "first", tmp_path / "replacement"
    path.write_bytes(b"same bytes")
    replacement.write_bytes(path.read_bytes())
    value = path.stat()
    os.utime(replacement, ns=(value.st_atime_ns, value.st_mtime_ns))
    before = identity.path_snapshot(path)
    os.replace(replacement, path)
    after = identity.path_snapshot(path)
    assert before.size == after.size
    assert before.modified == after.modified
    assert before.identity != after.identity


class FakeKernel:
    def __init__(self, *, legacy=False, filesystem="FAT32", attributes=0, file_type=1):
        self.legacy = legacy
        self.filesystem = filesystem
        self.attributes = attributes
        self.file_type = file_type
        self.closed = []
        self.opened = []

    def CreateFileW(self, path, access, sharing, security, creation, flags, template):
        self.opened.append((path, access, sharing, security, creation, flags, template))
        return 123

    def GetFileType(self, handle):
        assert handle == 123
        return self.file_type

    def GetFileInformationByHandleEx(self, handle, info_class, output, size):
        assert handle == 123
        cls = {0: identity._BasicInfo, 1: identity._StandardInfo, 18: identity._IdInfo}[info_class]
        assert size == ctypes.sizeof(cls)
        value = ctypes.cast(output, ctypes.POINTER(cls)).contents
        if info_class == 0:
            value.LastWriteTime, value.ChangeTime = 1234567890123, 1234567890456
            value.FileAttributes = self.attributes
        elif info_class == 1:
            value.EndOfFile = 19
            value.Directory = bool(self.attributes & 0x10)
        elif self.legacy:
            return 0
        else:
            value.VolumeSerialNumber = 77
            value.FileId[0], value.FileId[12] = 42, 99  # Preserve all 128 ID bits.
        return 1

    def GetVolumeInformationByHandleW(self, handle, *args):
        args[-2].value = self.filesystem
        return 1

    def GetFileInformationByHandle(self, handle, output):
        value = ctypes.cast(output, ctypes.POINTER(identity._LegacyInfo)).contents
        value.VolumeSerialNumber, value.FileIndexHigh, value.FileIndexLow = 77, 1, 42
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def native(monkeypatch, **kwargs):
    kernel = FakeKernel(**kwargs)
    monkeypatch.setattr(identity, "_windows_api", lambda: kernel)
    monkeypatch.setattr(identity, "_WINDOWS", True)
    return kernel


def test_windows_path_and_descriptor_share_native_identity_despite_crt_stat(tmp_path, monkeypatch):
    path = tmp_path / "original.xml"
    path.write_bytes(b"synthetic file only")
    kernel = native(monkeypatch)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 123))

    def forbidden_crt_stat(fd):
        pytest.fail("Windows identity must not mix CRT fstat fields with path stat fields")

    monkeypatch.setattr(identity.os, "fstat", forbidden_crt_stat)
    before, opened = identity.path_snapshot(path), identity.descriptor_snapshot(456)
    assert before.signature == opened.signature
    assert before.identity == (77, bytes([42] + [0] * 11 + [99] + [0] * 3))
    assert before.modified == 1234567890123
    assert before.changed == 1234567890456
    assert kernel.opened == [(str(path), 0x80, 7, None, 3, 0x02200000, None)]
    assert kernel.closed == [123]  # Path metadata handle only; caller keeps its handle.


@pytest.mark.parametrize("filesystem", ["FAT", "FAT32", "exFAT", "NTFS"])
def test_windows_supported_legacy_id_is_volume_and_file_index(monkeypatch, filesystem):
    native(monkeypatch, legacy=True, filesystem=filesystem)
    snapshot = identity.handle_snapshot(123)
    assert snapshot.identity == (77, ((1 << 32) | 42).to_bytes(16, "little"))


@pytest.mark.parametrize("filesystem", ["ReFS", "unrecognized"])
def test_windows_never_uses_truncated_identity_for_refs_or_unknown_fs(monkeypatch, filesystem):
    native(monkeypatch, legacy=True, filesystem=filesystem)
    with pytest.raises(OSError, match="128-bit"):
        identity.handle_snapshot(123)


@pytest.mark.parametrize(
    "attributes,regular,reparse", [(0, True, False), (0x10, False, False), (0x400, True, True)]
)
def test_windows_descriptor_attributes_preserve_directory_and_reparse_rejections(
    monkeypatch, attributes, regular, reparse
):
    native(monkeypatch, attributes=attributes)
    snapshot = identity.handle_snapshot(123)
    assert snapshot.is_regular is regular
    assert snapshot.is_reparse is reparse


@pytest.mark.parametrize("file_type", [0, 2, 3])
def test_windows_non_disk_handle_is_rejected_before_metadata_or_reads(monkeypatch, file_type):
    native(monkeypatch, file_type=file_type)
    with pytest.raises(OSError, match="disk file"):
        identity.handle_snapshot(123)


def test_windows_metadata_handle_closed_even_when_identity_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_bytes(b"original")
    kernel = native(monkeypatch, legacy=True, filesystem="ReFS")
    with pytest.raises(OSError):
        identity.path_snapshot(path)
    assert kernel.closed == [123]
