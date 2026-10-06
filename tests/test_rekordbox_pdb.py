import hashlib

import pytest

from djlib.domain.errors import AppError
from djlib.exporting import rekordbox_pdb
from djlib.exporting.usb_check import check_playlist
from tests import pdb_fixture as pdb


def expected(files):
    return [
        {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "label": name}
        for name, data in files
    ]


def test_reads_playlists_tracks_keys_and_unicode_names():
    data = pdb.library(
        {
            0: [pdb.track_row(7, "Delete", "/Contents/A/x.flac", 1234, 128.5, 2, 3)],
            2: [pdb.artist_row(3, "Ninajirachi")],
            5: [pdb.key_row(2, "F#m")],
            7: [
                pdb.tree_row(1, "Sets", folder=True),
                pdb.tree_row(4, "Tonight — Warm-up", parent=1),
            ],
            8: [pdb.entry_row(2, 7, 4), pdb.entry_row(1, 7, 4)],
        }
    )
    library = rekordbox_pdb.DeviceLibrary(data)

    [playlist] = library.playlists()  # folders are not playlists
    assert playlist["name"] == "Tonight — Warm-up" and playlist["track_ids"] == [7, 7]
    track = library.tracks()[7]
    assert track["title"] == "Delete" and track["artist"] == "Ninajirachi"
    assert track["file_path"] == "/Contents/A/x.flac" and track["file_size"] == 1234
    assert track["bpm"] == 128.5 and track["key"] == "F#m"


def test_skips_deleted_rows_and_spans_many_row_groups():
    rows = [pdb.key_row(n, f"K{n}") for n in range(1, 41)]
    library = rekordbox_pdb.DeviceLibrary(pdb.library({5: rows}, deleted={5: frozenset({0, 17})}))
    keys = library.keys()
    assert len(keys) == 38 and 1 not in keys and 18 not in keys and keys[40] == "K40"


def test_rejects_files_that_are_not_device_libraries(tmp_path):
    with pytest.raises(AppError):
        rekordbox_pdb.DeviceLibrary(b"not a database" * 4)
    with pytest.raises(AppError) as error:
        rekordbox_pdb.read(tmp_path)
    assert error.value.code == "DEVICE_LIBRARY_MISSING"


def test_check_playlist_requires_the_right_files_in_order(tmp_path):
    files = [("A/one.flac", b"first audio"), ("B/two.flac", b"second audio!")]
    pdb.stick(tmp_path, "Set 0", files)

    good = check_playlist(tmp_path, "Set 0", expected(files))
    assert good["found"] == 2 and good["in_order"] is True and good["playlist_on_device"]
    assert good["verified_by"] == "device_library_and_file_hashes"

    swapped = check_playlist(tmp_path, "Set 0", expected(files[::-1]))
    assert swapped["found"] == 0 and swapped["in_order"] is False

    (tmp_path / "Contents" / "B" / "two.flac").write_bytes(b"other audio!!")
    changed = check_playlist(tmp_path, "Set 0", expected(files))
    assert changed["found"] == 1 and changed["missing"] == ["B/two.flac"]


def test_check_playlist_falls_back_to_file_hashes_without_the_playlist(tmp_path):
    files = [("A/one.flac", b"first audio")]
    pdb.stick(tmp_path, "Other", files)
    result = check_playlist(tmp_path, "Set 0", expected(files))
    assert result["playlist_on_device"] is False and result["found"] == 1
    assert result["verified_by"] == "usb_file_hashes"
