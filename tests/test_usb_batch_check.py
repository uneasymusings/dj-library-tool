"""Overlapping playlists retain order/content checks without rereading shared USB audio."""

import hashlib
import os
import struct

import pytest

from djlib.domain.errors import AppError
from djlib.exporting import usb_check
from tests import pdb_fixture as pdb


def expected(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def shared_playlists(volume):
    one, two = b"first audio", b"other audio"
    contents = volume / "Contents"
    contents.mkdir()
    (contents / "one.mp3").write_bytes(one)
    (contents / "two.mp3").write_bytes(two)
    folder = volume / "PIONEER" / "rekordbox"
    folder.mkdir(parents=True)
    (folder / "export.pdb").write_bytes(
        pdb.library(
            {
                0: [
                    pdb.track_row(1, "One", "/Contents/one.mp3", len(one), 124, 1, 1),
                    pdb.track_row(2, "Two", "/Contents/two.mp3", len(two), 126, 1, 1),
                ],
                2: [pdb.artist_row(1, "Artist")],
                5: [pdb.key_row(1, "Am")],
                7: [pdb.tree_row(10, "All Music"), pdb.tree_row(20, "Set0")],
                8: [pdb.entry_row(1, 1, 10), pdb.entry_row(2, 2, 10), pdb.entry_row(1, 1, 20)],
            }
        )
    )
    return {"All Music": [expected(one), expected(two)], "Set0": [expected(one)]}


def test_batch_reads_shared_audio_once_and_reports_progress(tmp_path, monkeypatch):
    playlists = shared_playlists(tmp_path)
    real_hash, reads, progress = usb_check._sha256, [], []

    def hashed(path):
        reads.append(path)
        return real_hash(path)

    monkeypatch.setattr(usb_check, "_sha256", hashed)
    result = usb_check.check_playlists(
        tmp_path, playlists, on_progress=lambda done, total: progress.append((done, total))
    )
    assert result["verified"] is True
    assert result["unique_files_hashed"] == len(reads) == 2
    assert result["audio_bytes_read"] == len(b"first audio") + len(b"other audio")
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:], strict=False))
    assert result["playlists"]["Set0"]["in_order"] is True
    assert result["device_written"] is False
    # A new call must read fresh bytes rather than reuse the previous run's cache.
    usb_check.check_playlists(tmp_path, playlists)
    assert len(reads) == 4


def test_batch_does_not_hide_a_wrong_track_order(tmp_path):
    playlists = shared_playlists(tmp_path)
    playlists["All Music"].reverse()
    result = usb_check.check_playlists(tmp_path, playlists)
    assert result["verified"] is False
    assert result["playlists"]["All Music"]["in_order"] is False
    assert result["playlists"]["Set0"]["in_order"] is True


@pytest.mark.parametrize("target", ["library", "shared_audio"])
def test_batch_rejects_changes_during_verification(tmp_path, target):
    playlists = shared_playlists(tmp_path)
    changed = False

    def progress(done, total):
        nonlocal changed
        if done != 2 or changed:
            return
        changed = True
        if target == "library":
            path = tmp_path / "PIONEER" / "rekordbox" / "export.pdb"
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            # The second playlist references this same file; cached bytes must not pass.
            (tmp_path / "Contents" / "one.mp3").write_bytes(b"changed audio, different size")

    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, playlists, on_progress=progress)
    assert error.value.code == "DEVICE_CHANGED_DURING_CHECK"


def test_content_only_fallback_does_not_claim_playlist_verification(tmp_path):
    playlists = shared_playlists(tmp_path)
    result = usb_check.check_playlists(tmp_path, {"Not exported": playlists["Set0"]})
    assert result["verified"] is False
    assert result["playlists"]["Not exported"]["found"] == 1
    assert result["playlists"]["Not exported"]["playlist_on_device"] is False


def test_single_playlist_progress_preserves_the_existing_check(tmp_path):
    playlists = shared_playlists(tmp_path)
    progress = []
    result = usb_check.check_playlist(
        tmp_path,
        "All Music",
        playlists["All Music"],
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert result["in_order"] is True
    assert progress == [(0, 2), (1, 2), (2, 2)]


def test_empty_batch_is_not_a_successful_device_check(tmp_path):
    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, {})
    assert error.value.code == "INPUT_INVALID"


def test_same_size_playlist_reorder_with_unchanged_mtime_invalidates_the_batch(tmp_path):
    playlists = shared_playlists(tmp_path)
    path = tmp_path / "PIONEER" / "rekordbox" / "export.pdb"
    before = path.stat()
    changed = False

    def reorder_after_first_playlist(done, total):
        nonlocal changed
        if done != 2 or changed:
            return
        changed = True
        first, second = struct.pack("<III", 1, 1, 10), struct.pack("<III", 2, 2, 10)
        data = path.read_bytes()
        assert data.count(first) == data.count(second) == 1
        data = data.replace(first, struct.pack("<III", 1, 2, 10))
        data = data.replace(second, struct.pack("<III", 2, 1, 10))
        path.write_bytes(data)
        # Model a write within one FAT timestamp tick; file size is unchanged too.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, playlists, on_progress=reorder_after_first_playlist)
    assert error.value.code == "DEVICE_CHANGED_DURING_CHECK"


@pytest.mark.parametrize("name", ["exportLibrary.db", "exportLibrary.db-wal"])
def test_same_size_onelibrary_changes_with_unchanged_mtime_invalidate_the_batch(tmp_path, name):
    playlists = shared_playlists(tmp_path)
    path = tmp_path / "PIONEER" / "rekordbox" / name
    path.write_bytes(b"original library bytes")
    before = path.stat()
    changed = False

    def edit_after_first_playlist(done, total):
        nonlocal changed
        if done != 2 or changed:
            return
        changed = True
        path.write_bytes(b"modified library bytes")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, playlists, on_progress=edit_after_first_playlist)
    assert error.value.code == "DEVICE_CHANGED_DURING_CHECK"


@pytest.mark.parametrize("action", ["created", "removed"])
def test_onelibrary_files_appearing_or_disappearing_invalidate_the_batch(tmp_path, action):
    playlists = shared_playlists(tmp_path)
    path = tmp_path / "PIONEER" / "rekordbox" / "exportLibrary.db-wal"
    if action == "removed":
        path.write_bytes(b"a library transaction")
    changed = False

    def change_after_first_playlist(done, total):
        nonlocal changed
        if done != 2 or changed:
            return
        changed = True
        if action == "created":
            path.write_bytes(b"a library transaction")
        else:
            path.unlink()

    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, playlists, on_progress=change_after_first_playlist)
    assert error.value.code == "DEVICE_CHANGED_DURING_CHECK"


def test_database_change_while_fingerprinting_does_not_pass_as_a_stable_library(
    tmp_path, monkeypatch
):
    playlists = shared_playlists(tmp_path)
    path = tmp_path / "PIONEER" / "rekordbox" / "export.pdb"
    before, real_digest, changed = path.stat(), hashlib.file_digest, False

    def edit_after_read(stream, algorithm):
        nonlocal changed
        result = real_digest(stream, algorithm)
        if stream.name == str(path) and not changed:
            changed = True
            data = path.read_bytes()
            path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(usb_check.hashlib, "file_digest", edit_after_read)
    with pytest.raises(AppError) as error:
        usb_check.check_playlists(tmp_path, playlists)
    assert changed is True
    assert error.value.code == "DEVICE_CHANGED_DURING_CHECK"
