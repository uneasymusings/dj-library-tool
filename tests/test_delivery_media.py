"""Regression checks for audio semantics and exclusive working-copy publication."""

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from djlib.application.delivery import _reconcile_analysis
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.errors import AppError
from djlib.exporting import delivery_media
from djlib.exporting.targets import assess_track

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Working-copy regressions require real FFmpeg and ffprobe",
)


def track_for(source):
    inspected = inspect_audio(source)
    return {
        "recording_id": "regression-recording",
        "asset_revision_id": "regression-source-revision",
        "artist": "Test Artist",
        "title": "Regression tone",
        "version": "",
        "path": str(source),
        "sha256": inspected.sha256,
        "properties": inspected.as_dict(),
    }


@pytest.fixture
def original_flac(audio_factory):
    """Create every sample locally; exercise real resampling and lossless decoding."""
    wav = audio_factory("original-compatibility.wav", frequency=440, frames=88200)
    flac = wav.with_suffix(".flac")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-n",
            "-i",
            str(wav),
            "-c:a",
            "flac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-sample_fmt",
            "s32",
            str(flac),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    inspected = inspect_audio(flac)
    assert inspected.codec == "flac"
    assert inspected.sample_rate == 48000
    assert inspected.channels == 2
    assert inspected.bit_depth == 24
    return flac, {source: checksum(source) for source in (wav, flac)}


@pytest.mark.parametrize(
    ("mode", "codec", "suffix"),
    [("mp3_320", "mp3", ".mp3"), ("wav16_44100", "pcm_16", ".wav")],
)
def test_flac_compatibility_conversion_decodes_and_meets_legacy_audio_target(
    application, original_flac, mode, codec, suffix
):
    source, original_hashes = original_flac
    track = {
        **track_for(source),
        "artist": "Original Töne Artist",
        "title": "Compatibility tone",
        "version": "Generated trial",
    }
    prepared = delivery_media.prepare_media(
        source, track, application.workspace.exports / mode, mode
    )
    output = Path(prepared["path"])
    measured = inspect_audio(output)
    assert output.suffix == suffix
    assert prepared["conversion"] == mode
    assert measured.codec == codec
    assert measured.sample_rate == 44100
    assert measured.channels == 2
    assert measured.duration_seconds == pytest.approx(2, abs=0.06)
    if mode == "mp3_320":
        assert measured.bitrate_bps == 320000
    else:
        assert measured.bit_depth == 16
    assert measured.artist == track["artist"]
    assert measured.title == "Compatibility tone (Generated trial)"
    assert measured.as_dict() == prepared["properties"]
    assert delivery_media.pcm_hash(output) == prepared["pcm_sha256"]
    assert measured.sha256 == prepared["sha256"]
    assert prepared["source_sha256"] == original_hashes[source]
    assert assess_track("cdj-2000nxs", measured.as_dict()) == []
    assert {path: checksum(path) for path in original_hashes} == original_hashes


def test_preserved_flac_remains_unsupported_for_cdj_2000nxs(application, original_flac):
    source, original_hashes = original_flac
    prepared = delivery_media.prepare_media(
        source, track_for(source), application.workspace.exports / "preserved-flac", "preserve"
    )
    output = Path(prepared["path"])
    measured = inspect_audio(output)
    assert output.suffix == ".flac"
    assert prepared["conversion"] == "working_copy"
    assert measured.codec == "flac"
    assert measured.artist == "Test Artist"
    assert measured.title == "Regression tone"
    assert delivery_media.pcm_hash(output) == delivery_media.pcm_hash(source)
    assert assess_track("cdj-2000nxs", measured.as_dict()) == ["TARGET_CODEC_UNSUPPORTED"]
    assert {path: checksum(path) for path in original_hashes} == original_hashes


def test_existing_lower_bitrate_mp3_is_reused_without_reencoding(
    application, original_flac, monkeypatch
):
    source, original_hashes = original_flac
    mp3 = source.with_suffix(".mp3")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-n",
            "-i",
            str(source),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            str(mp3),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    track = track_for(mp3)
    original_hashes[mp3] = track["sha256"]
    assert track["properties"]["bitrate_bps"] == 128000
    source_pcm = delivery_media.pcm_hash(mp3)
    real_run = subprocess.run
    commands = []

    def record_real_command(command, *args, **kwargs):
        commands.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(delivery_media.subprocess, "run", record_real_command)
    prepared = delivery_media.prepare_media(
        mp3, track, application.workspace.exports / "reused-mp3", "mp3_320"
    )
    measured = inspect_audio(Path(prepared["path"]))
    assert prepared["conversion"] == "working_copy"
    assert measured.codec == "mp3"
    assert measured.bitrate_bps == 128000
    assert measured.sample_rate == 44100
    assert measured.artist == track["artist"]
    assert measured.title == track["title"]
    assert prepared["pcm_sha256"] == source_pcm
    assert assess_track("cdj-2000nxs", measured.as_dict()) == []
    assert commands
    assert all("libmp3lame" not in command and "-b:a" not in command for command in commands)
    assert {path: checksum(path) for path in original_hashes} == original_hashes


def test_sample_rate_header_change_is_not_a_tag_only_edit(application, audio_factory):
    source = audio_factory("sample-rate.wav", frequency=440, frames=44100)
    source_hash = checksum(source)
    prepared = delivery_media.prepare_media(
        source, track_for(source), application.workspace.exports / "rate-test", "preserve"
    )
    working = Path(prepared["path"])
    original_pcm = delivery_media.pcm_hash(working)
    content = bytearray(working.read_bytes())
    # Change only RIFF fmt sample-rate/byte-rate fields; sample bytes and ID3
    # tags stay identical. Playback pitch/tempo still change substantially.
    offset = 12
    while offset + 8 <= len(content):
        chunk_id, size = struct.unpack_from("<4sI", content, offset)
        if chunk_id == b"fmt ":
            channels = struct.unpack_from("<H", content, offset + 10)[0]
            bits = struct.unpack_from("<H", content, offset + 22)[0]
            struct.pack_into("<I", content, offset + 12, 48000)
            struct.pack_into("<I", content, offset + 16, 48000 * channels * (bits // 8))
            break
        offset += 8 + size + (size % 2)
    else:
        pytest.fail("Generated WAV has no fmt chunk")
    working.write_bytes(content)
    changed = inspect_audio(working)
    assert changed.sample_rate == 48000
    assert changed.duration_seconds == pytest.approx(44100 / 48000)
    assert delivery_media.pcm_hash(working) == original_pcm

    with pytest.raises(AppError) as error:
        _reconcile_analysis(application, {"tracks": [prepared]})
    assert error.value.code == "AUDIO_CHANGED"
    assert checksum(source) == source_hash


def test_destination_created_during_preparation_is_never_overwritten(
    application, audio_factory, monkeypatch
):
    source = audio_factory("promotion-race.wav", frequency=550)
    track = track_for(source)
    directory = application.workspace.exports / "promotion-race"
    destination = directory / f"Test Artist - Regression tone-{track['sha256'][:16]}.wav"
    interloper = b"Existing user bytes created while the working copy was being verified"
    real_pcm_hash = delivery_media.pcm_hash
    calls = []

    def create_destination_before_publication(path):
        digest = real_pcm_hash(path)
        with destination.open("xb") as stream:
            stream.write(interloper)
        calls.append(path)
        return digest

    # This seam runs after the early exists check and before publication. The
    # old os.replace implementation would silently overwrite these bytes.
    monkeypatch.setattr(delivery_media, "pcm_hash", create_destination_before_publication)
    with pytest.raises(FileExistsError):
        delivery_media.prepare_media(source, track, directory, "preserve")
    assert len(calls) == 1
    assert destination.read_bytes() == interloper
    assert checksum(source) == track["sha256"]
    assert not list(directory.glob(".prepare-*"))
