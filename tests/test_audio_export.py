"""Actual decoding and interchange data, including corruption and URI handling."""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

import pytest

from djlib.audio.inspection import checksum, inspect_audio, labels
from djlib.domain.errors import AppError
from djlib.exporting.handoff import device_preflight, rekordbox_xml


def test_complete_wave_measured_and_tagged(audio_factory):
    path = audio_factory(artist="Artist", title="Track")
    inspection = inspect_audio(path)
    assert inspection.duration_seconds == pytest.approx(0.1)
    assert inspection.channels == 1 and inspection.sample_rate == 44100
    assert inspection.sha256 == checksum(path)
    assert labels(path) == ("Artist", "Track")


@pytest.mark.parametrize("kind", ["truncated", "empty", "wrong_bytes", "wrong_extension"])
def test_corrupt_audio_rejected(audio_factory, kind):
    path = audio_factory(frames=0 if kind == "empty" else 4410)
    if kind == "truncated":
        path.write_bytes(path.read_bytes()[:-500])
    elif kind == "wrong_bytes":
        path.write_bytes(b"not audio")
    elif kind == "wrong_extension":
        path = path.rename(path.with_suffix(".exe"))
    with pytest.raises(AppError):
        inspect_audio(path)


@pytest.mark.parametrize("extension", [".flac", ".mp3", ".m4a", ".aiff"])
def test_real_ffmpeg_formats(audio_factory, extension):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    source = audio_factory()
    path = source.with_suffix(extension)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), str(path)], check=True, timeout=20
    )
    result = inspect_audio(path)
    assert result.duration_seconds > 0 and result.sample_rate == 44100
    assert result.sha256 == checksum(path)


def test_missing_decoder_actionable(audio_factory, monkeypatch):
    path = audio_factory().rename(audio_factory().with_suffix(".mp3"))
    monkeypatch.setattr("djlib.audio.inspection.shutil.which", lambda _: None)
    with pytest.raises(AppError) as error:
        inspect_audio(path)
    assert error.value.code == "DEPENDENCY_REQUIRED"


def test_xml_escapes_labels_and_encodes_paths(audio_factory):
    path = audio_factory("space & 音楽.wav")
    snapshot = {
        "name": "A & B <warmup>",
        "tracks": [
            {
                "path": str(path),
                "artist": "A & B",
                "title": "<Track>",
                "version": "Dub",
                "properties": {"duration_seconds": 1.2},
            }
        ],
    }
    xml = rekordbox_xml(snapshot)
    root = ET.fromstring(xml)
    track = root.find("COLLECTION/TRACK")
    uri = urlsplit(track.attrib["Location"])
    assert uri.scheme == "file" and uri.netloc == "localhost"
    assert unquote(uri.path).endswith("space & 音楽.wav")
    assert track.attrib["Artist"] == "A & B" and track.attrib["Mix"] == "Dub"
    assert root.find("PLAYLISTS/NODE/NODE/TRACK").attrib["Key"] == track.attrib["TrackID"]
    assert root.find("COLLECTION").attrib["Entries"] == "1"
    assert root.find("COLLECTION/TRACK/TEMPO") is None


def test_preflight_is_read_only_and_truthful(tmp_path):
    before = list(tmp_path.iterdir())
    report = device_preflight(str(tmp_path), 10**30)
    assert not report["has_requested_space"] and not report["writes_performed"]
    assert report["player_compatibility"] == "not_verified"
    assert report["device_state"] == "not_exported"
    assert list(tmp_path.iterdir()) == before
    json.dumps(report)


def test_preflight_missing_device(tmp_path):
    with pytest.raises(AppError) as error:
        device_preflight(str(tmp_path / "missing"))
    assert error.value.code == "DEVICE_UNAVAILABLE"
