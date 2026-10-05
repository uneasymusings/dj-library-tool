"""Device evidence cannot imply native import, native export, or player readiness."""

import hashlib
import json
import os
import plistlib
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from djlib.domain.errors import AppError
from djlib.exporting import device_readback as device


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    root = tmp_path / "USB"
    root.mkdir()
    monkeypatch.setattr(Path, "is_mount", lambda self: self == root)

    def identity(path):
        info = path.stat()
        return {
            "value": "VolumeUUID:fixture",
            "method": "diskutil_uuid",
            "confidence": "strong",
            "stat_device": info.st_dev,
            "stat_inode": info.st_ino,
            "filesystem": "FAT32",
            "partition_scheme": None,
        }

    monkeypatch.setattr(device, "_volume_identity", identity)
    return root


def assert_not_ready(report):
    assert report["native_export_verified"] is False
    assert report["hardware_verified"] is False
    assert report["writes_performed"] is False
    assert report["player_compatibility"] == "unknown"
    assert report["readiness_confidence"] == "unknown"


def test_ordinary_directory_never_scanned(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "is_mount", lambda _: False)
    monkeypatch.setattr(device.platform, "system", lambda: "Linux")
    monkeypatch.setattr(device, "_hash_inventory", lambda *_: pytest.fail("Not a mount"))
    report = device.inspect_device(str(tmp_path), [digest(b"missing")])
    assert report["status"] == "not_mounted"
    assert report["hash_inventory"] is None
    assert report["volume_identity"]["confidence"] == "weak"
    assert_not_ready(report)


def test_old_database_markers_are_only_existence_evidence(mounted, monkeypatch):
    native = mounted / "PIONEER/rekordbox"
    native.mkdir(parents=True)
    (native / "export.pdb").write_bytes(b"old database not parsed")
    (native / "exportLibrary.db").write_bytes(b"old database not parsed")
    (mounted / "_Serato_/Subcrates").mkdir(parents=True)
    before = sorted(str(path.relative_to(mounted)) for path in mounted.rglob("*"))
    with monkeypatch.context() as context:
        context.setattr(os, "scandir", lambda *_: pytest.fail("Default must not recurse"))
        report = device.inspect_device(str(mounted))
    assert report["status"] == "inventory_only"
    assert report["volume_unchanged"] is True
    for marker in report["native_markers"].values():
        assert marker["present"] is True
        assert marker["evidence"] == "existence_only"
        assert marker["freshness"] == "unknown"
        assert marker["contents_verified"] is False
    assert report["hash_inventory"]["scan_performed"] is False
    assert sorted(str(path.relative_to(mounted)) for path in mounted.rglob("*")) == before
    assert (native / "export.pdb").read_bytes() == b"old database not parsed"
    assert_not_ready(report)
    json.dumps(report)


def test_expected_hashes_match_contents_and_serato_audio_without_writes(mounted):
    files = {
        "Contents/Artist/Album/track.FLAC": b"recording one",
        "Music/Serato crate/track.mp3": b"recording two",
    }
    for name, value in files.items():
        path = mounted / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    expected = [digest(value) for value in files.values()]
    report = device.inspect_device(str(mounted), expected + [expected[0].upper()])
    inventory = report["hash_inventory"]
    assert report["status"] == "verified_hashes"
    assert inventory["matched"] == expected
    assert inventory["missing"] == []
    assert inventory["files_hashed"] == 2
    assert inventory["complete"] is True
    assert {p for match in inventory["matches"] for p in match["relative_paths"]} == set(files)
    assert {name: (mounted / name).read_bytes() for name in files} == files
    assert_not_ready(report)


def test_missing_or_corrupt_bytes_do_not_match_expected(mounted):
    (mounted / "track.mp3").write_bytes(b"truncated recording")
    wanted = digest(b"complete recording")
    report = device.inspect_device(str(mounted), [wanted])
    assert report["status"] == "missing_hashes"
    assert report["hash_inventory"]["missing"] == [wanted]
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["complete"] is True
    assert report["hash_inventory"]["traversal_complete"] is True
    assert_not_ready(report)


def test_symlink_files_directories_and_native_markers_never_followed(mounted, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.mp3").write_bytes(b"outside")
    (outside / "rekordbox").mkdir()
    (outside / "rekordbox/export.pdb").write_bytes(b"outside database")
    (mounted / "link.mp3").symlink_to(outside / "outside.mp3")
    (mounted / "Contents").symlink_to(outside, target_is_directory=True)
    (mounted / "PIONEER").symlink_to(outside, target_is_directory=True)
    report = device.inspect_device(str(mounted), [digest(b"outside")])
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["files_hashed"] == 0
    assert report["hash_inventory"]["symlinks_skipped"] == 3
    assert not report["native_markers"]["device_library"]["present"]


def test_audio_hardlinks_are_hashed_once(mounted):
    source = mounted / "track.mp3"
    source.write_bytes(b"one inode")
    os.link(source, mounted / "again.mp3")
    report = device.inspect_device(str(mounted), [digest(b"one inode"), digest(b"missing")])
    assert report["hash_inventory"]["files_hashed"] == 1
    assert report["hash_inventory"]["files_examined"] == 2
    assert len(report["hash_inventory"]["matches"][0]["relative_paths"]) == 2


def test_volume_replacement_invalidates_even_successful_hashes(mounted, monkeypatch):
    (mounted / "track.mp3").write_bytes(b"music")
    original = device._volume_identity
    calls = []

    def replaced(path):
        value = original(path)
        calls.append(path)
        if len(calls) == 2:
            value["value"] = "VolumeUUID:replacement"
        return value

    monkeypatch.setattr(device, "_volume_identity", replaced)
    report = device.inspect_device(str(mounted), [digest(b"music")])
    assert report["status"] == "volume_changed"
    assert report["volume_unchanged"] is False
    assert report["hash_inventory"]["complete"] is False
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["errors"][-1]["code"] == "VOLUME_CHANGED"
    assert_not_ready(report)


def test_file_limit_reports_incomplete_not_absent(mounted, monkeypatch):
    for number in range(3):
        (mounted / f"{number}.mp3").write_bytes(str(number).encode())
    monkeypatch.setattr(device, "MAX_AUDIO_FILES", 2)
    report = device.inspect_device(str(mounted), [digest(b"missing")])
    assert report["status"] == "incomplete"
    assert report["hash_inventory"]["complete"] is False
    assert report["hash_inventory"]["files_hashed"] == 2
    assert report["hash_inventory"]["errors"] == [{"code": "SCAN_FILE_LIMIT"}]


def test_byte_limit_is_bounded(mounted, monkeypatch):
    (mounted / "track.mp3").write_bytes(b"large recording")
    monkeypatch.setattr(device, "MAX_SCAN_BYTES", 1)
    report = device.inspect_device(str(mounted), [digest(b"large recording")])
    assert report["status"] == "incomplete"
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["errors"] == [{"code": "SCAN_BYTE_LIMIT"}]


def test_no_follow_unavailable_fails_closed(mounted, monkeypatch):
    (mounted / "track.mp3").write_bytes(b"music")
    # Exercise the unsupported backend case on every OS, while normal Windows
    # positive cases above use its native relative-handle implementation.
    monkeypatch.setattr(device, "_WINDOWS", False)
    monkeypatch.setattr(os, "supports_dir_fd", set())
    report = device.inspect_device(str(mounted), [digest(b"music")])
    assert report["status"] == "incomplete"
    assert report["hash_inventory"]["files_hashed"] == 0
    assert report["hash_inventory"]["errors"] == [{"code": "SAFE_READBACK_UNSUPPORTED"}]
    assert_not_ready(report)


def test_file_changed_during_hash_is_not_verified(mounted, monkeypatch):
    path = mounted / "track.mp3"
    path.write_bytes(b"original")
    expected = digest(b"original")
    sha256 = hashlib.sha256

    class ChangedDuringRead:
        def __init__(self):
            self.hasher = sha256()

        def update(self, chunk):
            self.hasher.update(chunk)
            path.write_bytes(b"modified")

        def hexdigest(self):
            return self.hasher.hexdigest()

    monkeypatch.setattr(device.hashlib, "sha256", ChangedDuringRead)
    report = device.inspect_device(str(mounted), [expected])
    assert report["status"] == "incomplete"
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["errors"][0]["code"] == "READ_FAILED"


def test_diskutil_uuid_filesystem_and_partition_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout=plistlib.dumps(
                {
                    "VolumeUUID": "1234",
                    "DeviceIdentifier": "disk4s1",
                    "FilesystemName": "ExFAT",
                    "PartitionMapScheme": "GUID_partition_scheme",
                    "Content": "Microsoft Basic Data",
                }
            )
        )

    monkeypatch.setattr(device.subprocess, "run", run)
    result = device._volume_identity(tmp_path)
    assert result["value"] == "VolumeUUID:1234"
    assert result["confidence"] == "strong"
    assert result["filesystem"] == "ExFAT"
    assert result["partition_scheme"] == "GUID_partition_scheme"
    assert calls[0][0] == ["/usr/sbin/diskutil", "info", "-plist", str(tmp_path)]
    assert calls[0][1]["timeout"] == device.DISKUTIL_TIMEOUT


def test_diskutil_timeout_falls_back_to_explicitly_weak_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(device.subprocess, "run", timeout)
    result = device._volume_identity(tmp_path)
    assert result["confidence"] == "weak"
    assert result["method"] == "stat_device_and_inode"
    assert result["filesystem"] is None
    assert "weak" in result["warning"]


@pytest.mark.parametrize("scheme", ["FDisk_partition_scheme", "GUID_partition_scheme"])
def test_partition_scheme_read_from_parent_whole_disk(tmp_path, monkeypatch, scheme):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        data = (
            {"Content": scheme, "WholeDisk": True, "DeviceIdentifier": "disk4"}
            if args[-1] == "/dev/disk4"
            else {"VolumeUUID": "volume-id", "ParentWholeDisk": "disk4", "TypeBundle": "msdos"}
        )
        return SimpleNamespace(stdout=plistlib.dumps(data))

    monkeypatch.setattr(device.subprocess, "run", run)
    result = device._volume_identity(tmp_path)
    assert result["value"] == "VolumeUUID:volume-id"
    assert result["confidence"] == "strong"
    assert result["partition_scheme"] == scheme
    assert result["partition_scheme_source"] == "parent_diskutil_info"
    assert result["parent_device_identifier"] == "disk4"
    assert result["filesystem"] == "msdos"
    assert [args[-1] for args, _ in calls] == [str(tmp_path), "/dev/disk4"]
    assert all(kwargs["timeout"] == device.DISKUTIL_TIMEOUT for _, kwargs in calls)


def test_parent_query_skipped_when_scheme_known(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    calls = []

    def info(value):
        calls.append(value)
        return {"ParentWholeDisk": "disk4", "PartitionMapScheme": "GUID_partition_scheme"}

    monkeypatch.setattr(device, "_diskutil_info", info)
    result = device._volume_identity(tmp_path)
    assert result["partition_scheme_source"] == "volume_diskutil_info"
    assert calls == [str(tmp_path)]


def test_specific_filesystem_personality_is_not_replaced_by_msdos_family(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        device,
        "_diskutil_info",
        lambda _: {
            "FilesystemType": "msdos",
            "TypeBundle": "msdos",
            "FileSystemPersonality": "MS-DOS FAT16",
            "VolumeUUID": "volume-id",
        },
    )
    result = device._volume_identity(tmp_path)
    assert result["filesystem"] == "MS-DOS FAT16"
    assert result["filesystem_type"] == "msdos"


def test_parent_timeout_preserves_volume_uuid_and_unknown_partition(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    calls = []

    def info(value):
        calls.append(value)
        if value == "/dev/disk4":
            raise subprocess.TimeoutExpired(["diskutil", value], 5)
        return {"VolumeUUID": "volume-id", "PartOfWhole": "disk4", "TypeBundle": "msdos"}

    monkeypatch.setattr(device, "_diskutil_info", info)
    result = device._volume_identity(tmp_path)
    assert result["value"] == "VolumeUUID:volume-id"
    assert result["confidence"] == "strong"
    assert result["partition_scheme"] is None
    assert result["filesystem"] == "msdos"
    assert "partition_warning" in result
    assert len(calls) == 2


def test_unrecognized_parent_is_not_queried_or_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    calls = []

    def info(value):
        calls.append(value)
        return {"ParentWholeDisk": "../../other-device", "Content": "Microsoft Basic Data"}

    monkeypatch.setattr(device, "_diskutil_info", info)
    result = device._volume_identity(tmp_path)
    assert result["partition_scheme"] is None
    assert result["parent_device_identifier"] is None
    assert calls == [str(tmp_path)]


def test_transient_identity_probe_failure_does_not_claim_replacement(mounted, monkeypatch):
    (mounted / "track.mp3").write_bytes(b"music")
    original = device._volume_identity
    calls = []

    def lost_uuid(path):
        value = original(path)
        calls.append(path)
        if len(calls) == 2:
            value.update(value="stat:fallback", confidence="weak", method="stat_device_and_inode")
        return value

    monkeypatch.setattr(device, "_volume_identity", lost_uuid)
    report = device.inspect_device(str(mounted), [digest(b"music")])
    assert report["status"] == "identity_unverified"
    assert report["volume_unchanged"] is None
    assert report["hash_inventory"]["complete"] is False
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["errors"][-1]["code"] == "VOLUME_IDENTITY_UNVERIFIED"
    assert_not_ready(report)


@pytest.mark.parametrize("value", [["not-sha256"], [["nested"]], "not-a-list"])
def test_invalid_hashes_rejected_before_scanning(tmp_path, value):
    with pytest.raises(AppError) as error:
        device.inspect_device(str(tmp_path), value)
    assert error.value.code == "EXPECTED_HASH_INVALID"


def test_missing_path_is_actionable(tmp_path):
    with pytest.raises(AppError) as error:
        device.inspect_device(str(tmp_path / "missing"))
    assert error.value.code == "DEVICE_UNAVAILABLE"


def test_windows_directory_records_preserve_unicode_and_skip_dot_entries():
    data = bytearray()
    for index, (name, attributes) in enumerate([(".", 0x10), ("音楽 🎵.mp3", 0x20)]):
        encoded = name.encode("utf-16-le")
        length = (64 + len(encoded) + 7) // 8 * 8
        record = bytearray(length)
        struct.pack_into("<I", record, 0, length if index == 0 else 0)
        struct.pack_into("<II", record, 56, attributes, len(encoded))
        record[64 : 64 + len(encoded)] = encoded
        data.extend(record)
    assert list(device._windows_directory_entries(data)) == [("音楽 🎵.mp3", 0x20)]


@pytest.mark.parametrize("kind", ["short", "offset", "name-length", "traversal", "stream"])
def test_windows_directory_records_reject_malformed_or_escaping_names(kind):
    name = (
        "../outside.mp3"
        if kind == "traversal"
        else "track.mp3:stream"
        if kind == "stream"
        else "tone.mp3"
    )
    encoded = name.encode("utf-16-le")
    record = bytearray(64 + len(encoded))
    struct.pack_into("<II", record, 56, 0x20, len(encoded))
    record[64:] = encoded
    if kind == "short":
        record = record[:12]
    elif kind == "offset":
        struct.pack_into("<I", record, 0, 16)
    elif kind == "name-length":
        struct.pack_into("<I", record, 60, 10000)
    with pytest.raises(OSError):
        list(device._windows_directory_entries(record))


def test_windows_volume_guid_probe_and_explicit_weak_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        device,
        "_windows_volume_info",
        lambda _: {
            "value": "\\\\?\\Volume{fixture}\\",
            "method": "windows_volume_guid",
            "confidence": "strong",
            "filesystem": "exFAT",
            "filesystem_type": "exFAT",
        },
    )
    result = device._volume_identity(tmp_path)
    assert result["method"] == "windows_volume_guid" and result["confidence"] == "strong"
    assert result["filesystem"] == "exFAT" and result["partition_scheme"] is None

    def unavailable(_):
        raise OSError("Mount no longer available")

    monkeypatch.setattr(device, "_windows_volume_info", unavailable)
    result = device._volume_identity(tmp_path)
    assert result["confidence"] == "weak" and "GUID unavailable" in result["warning"]


@pytest.mark.skipif(os.name != "nt", reason="Exercises Windows native directory handles")
def test_windows_native_unicode_scan_and_handles_are_closed(mounted, monkeypatch):
    path = mounted / "音楽 🎵" / "opening 🎶.mp3"
    path.parent.mkdir()
    path.write_bytes(b"original generated bytes")
    # The Windows backend must never fall back to path-based directory enumeration.
    monkeypatch.setattr(os, "scandir", lambda *_: pytest.fail("Use a held directory handle"))
    report = device.inspect_device(str(mounted), [digest(path.read_bytes())])
    assert report["status"] == "verified_hashes"
    assert report["hash_inventory"]["matches"][0]["relative_paths"] == ["音楽 🎵/opening 🎶.mp3"]
    # Handles deny delete sharing while held; these operations establish cleanup.
    path.rename(path.with_name("renamed.mp3"))
    path.parent.rename(mounted / "renamed folder")
    assert_not_ready(report)


@pytest.mark.skipif(os.name != "nt", reason="Exercises native Windows junction boundaries")
def test_windows_junctions_never_contribute_audio_or_database_markers(mounted, tmp_path):
    outside = tmp_path / "outside"
    (outside / "rekordbox").mkdir(parents=True)
    (outside / "audio.mp3").write_bytes(b"outside bytes")
    (outside / "rekordbox/export.pdb").write_bytes(b"outside database")
    for name in ("PIONEER", "Contents"):
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(mounted / name), str(outside)],
            check=True,
            capture_output=True,
        )
    report = device.inspect_device(str(mounted), [digest(b"outside bytes")])
    assert report["hash_inventory"]["files_hashed"] == 0
    assert report["hash_inventory"]["symlinks_skipped"] == 2
    assert report["hash_inventory"]["matched"] == []
    assert not report["native_markers"]["device_library"]["present"]


@pytest.mark.skipif(os.name != "nt", reason="Exercises Windows replacement between enumerate/open")
def test_windows_reparse_replacement_after_enumeration_is_not_read(mounted, tmp_path, monkeypatch):
    path, outside = mounted / "track.mp3", tmp_path / "outside.mp3"
    path.write_bytes(b"inside bytes")
    outside.write_bytes(b"outside bytes")
    original = device._WindowsReadback.open_child
    replaced = False

    def replace(self, parent, name, directory, **options):
        nonlocal replaced
        if name == "track.mp3" and not replaced:
            path.unlink()
            path.symlink_to(outside)
            replaced = True
        return original(self, parent, name, directory, **options)

    monkeypatch.setattr(device._WindowsReadback, "open_child", replace)
    report = device.inspect_device(str(mounted), [digest(b"outside bytes")])
    assert replaced
    assert report["hash_inventory"]["matched"] == []
    assert report["hash_inventory"]["files_hashed"] == 0
    assert report["hash_inventory"]["symlinks_skipped"] == 1
