import hashlib

import pytest

from djlib.domain.errors import AppError
from djlib.exporting.usb_check import check_tracks, library_state


def track(data: bytes, label: str) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "label": label}


def test_matches_by_content_not_by_name(tmp_path):
    contents = tmp_path / "Contents" / "Artist" / "Album"
    contents.mkdir(parents=True)
    (contents / "renamed copy.flac").write_bytes(b"first track audio")
    (contents / "second.flac").write_bytes(b"different bytes!!")  # same size, other audio
    (contents / "._second.flac").write_bytes(b"second track audio")  # macOS sidecar, ignored
    expected = [track(b"first track audio", "One"), track(b"second track audio", "Two")]

    result = check_tracks(tmp_path, expected)

    assert result["found"] == 1 and result["expected"] == 2
    assert result["missing"] == ["Two"]
    assert result["device_written"] is False


def test_requires_a_rekordbox_export_and_reads_library_state(tmp_path):
    with pytest.raises(AppError) as error:
        check_tracks(tmp_path, [])
    assert error.value.code == "DEVICE_UNAVAILABLE"
    assert library_state(tmp_path) == {}
    (tmp_path / "PIONEER" / "rekordbox").mkdir(parents=True)
    (tmp_path / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"pdb")
    assert set(library_state(tmp_path)) == {"export.pdb"}
