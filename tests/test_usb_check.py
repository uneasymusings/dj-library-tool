import hashlib

import pytest

from djlib.domain.errors import AppError
from djlib.exporting.usb_check import check_tracks, library_state, wait_for_copy


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


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_waits_until_every_file_arrived_and_the_stick_settled(tmp_path):
    folder = tmp_path / "Contents" / "Artist"
    folder.mkdir(parents=True)
    library = tmp_path / "PIONEER" / "rekordbox"
    library.mkdir(parents=True)
    audio = [b"first track audio", b"second track!"]
    expected = [track(data, str(n)) for n, data in enumerate(audio)]
    before = library_state(tmp_path)
    clock, seen = FakeClock(), []

    def progress(count):
        seen.append(count)
        # rekordbox copies one file per check, rewriting its device library as it goes.
        if count < len(audio):
            (folder / f"{count}.flac").write_bytes(audio[count])
            (library / "export.pdb").write_bytes(b"x" * (count + 1))

    done = wait_for_copy(
        tmp_path, expected, before, on_progress=progress, clock=clock, sleep=clock.sleep
    )

    assert done is True
    assert seen[:3] == [0, 1, 2] and seen[-1] == 2


def test_gives_up_when_the_stick_stops_changing(tmp_path):
    (tmp_path / "Contents").mkdir()
    (tmp_path / "PIONEER" / "rekordbox").mkdir(parents=True)
    before = library_state(tmp_path)
    (tmp_path / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"new")
    clock = FakeClock()

    done = wait_for_copy(
        tmp_path, [track(b"never copied", "x")], before, stall=30, clock=clock, sleep=clock.sleep
    )

    assert done is False and 30 <= clock.now < 40
