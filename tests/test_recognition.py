"""Set audio, windows and song recognition, without the network or a real Shazam."""

import io
import json
import sys
import wave

import pytest

from djlib.audio import windows
from djlib.domain.errors import AppError
from djlib.sources import web

SET_URL = "https://www.youtube.com/watch?v=keinemusik"


def frames(wav: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(wav)) as audio:
        return (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getnframes(),
        )


def test_windows_cut_mono_16k_wav_from_a_recording(audio_factory, tmp_path):
    path = audio_factory("set.wav", frames=44100 * 3)
    assert windows.duration(path) == pytest.approx(3.0, abs=0.01)
    channels, width, rate, count = frames(windows.window_wav(path, 0.5, 2.0))
    assert (channels, width, rate) == (1, 2, 16000) and 31800 <= count <= 32000
    # The last window is cut short by the end of the recording.
    assert 15800 <= frames(windows.window_wav(path, 2.0, 12.0))[3] <= 16000
    with pytest.raises(AppError) as empty:
        windows.window_wav(path, 10.0, 2.0)
    assert empty.value.code == "AUDIO_WINDOW_EMPTY"
    (tmp_path / "notes.wav").write_text("not audio")
    for call in (
        lambda: windows.duration(tmp_path / "notes.wav"),
        lambda: windows.window_wav(tmp_path / "notes.wav", 0.0),
    ):
        with pytest.raises(AppError) as broken:
            call()
        assert broken.value.code == "AUDIO_INVALID"


def test_windows_refuse_bad_ranges_and_need_ffmpeg(audio_factory, monkeypatch):
    path = audio_factory("set.wav")
    for start, length in ((-1.0, 12.0), (0.0, 0.0), (0.0, 61.0)):
        with pytest.raises(AppError) as invalid:
            windows.window_wav(path, start, length)
        assert invalid.value.code == "INPUT_INVALID"
    monkeypatch.setattr(windows.shutil, "which", lambda _: None)
    with pytest.raises(AppError) as missing:
        windows.window_wav(path, 0.0)
    assert missing.value.code == "DEPENDENCY_REQUIRED" and "FFmpeg" in missing.value.message


def yt_dlp_saves(tmp_path, seen, *, name="set.webm", output=None):
    async def fake(args, **options):
        seen.append((args, options))
        (tmp_path / name).write_bytes(b"opus")
        info = {
            "id": "keinemusik",
            "title": "Keinemusik live",
            "uploader": "Keinemusik",
            "duration": 3721.5,
            "extractor_key": "Youtube",
            "filepath": str(tmp_path / name),
            "comments": ["never stored"],
        }
        return output if output is not None else b"[info] noise\n" + json.dumps(info).encode()

    return fake


async def test_set_audio_is_kept_as_published_with_a_receipt(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "command", lambda: ["yt-dlp"])
    monkeypatch.setattr(web, "run", yt_dlp_saves(tmp_path, seen))
    receipt = await web.fetch_set_audio(SET_URL, tmp_path)
    assert receipt == {
        "url": SET_URL,
        "provider": "Youtube",
        "source_id": "keinemusik",
        "title": "Keinemusik live",
        "uploader": "Keinemusik",
        "duration_seconds": 3721.5,
        "path": str(tmp_path / "set.webm"),
    }
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt
    (args, options) = seen[0]
    assert args[args.index("--format") + 1] == "bestaudio/best"
    assert "--extract-audio" not in args and "--audio-format" not in args
    assert args[args.index("--max-filesize") + 1] == "2G"
    assert args[args.index("--match-filters") + 1] == "duration <= 21600 & !is_live"
    assert args[args.index("--output") + 1] == str(tmp_path / "set.%(ext)s")
    assert args[-2:] == ["--", SET_URL]
    assert options["timeout"] == 1800 and options["download_dir"] == tmp_path
    assert options["max_bytes"] == web.SET_AUDIO_BYTES > 2 * 1024**3 > web.MAX_DOWNLOAD_BYTES


async def test_set_audio_reuses_the_saved_file(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "command", lambda: ["yt-dlp"])
    monkeypatch.setattr(web, "run", yt_dlp_saves(tmp_path, seen))
    first = await web.fetch_set_audio(SET_URL, tmp_path)
    assert await web.fetch_set_audio(SET_URL, tmp_path) == first
    assert len(seen) == 1
    # Another set, a missing file or an unreadable receipt mean fetching again.
    await web.fetch_set_audio("https://soundcloud.com/keinemusik/live", tmp_path)
    (tmp_path / "set.webm").unlink()
    await web.fetch_set_audio("https://soundcloud.com/keinemusik/live", tmp_path)
    (tmp_path / "receipt.json").write_text("broken")
    await web.fetch_set_audio("https://soundcloud.com/keinemusik/live", tmp_path)
    assert len(seen) == 4


async def test_set_audio_skipped_by_the_limits_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "command", lambda: ["yt-dlp"])
    # yt-dlp prints nothing when a filter (live, over six hours, over 2 GB) skips the set.
    monkeypatch.setattr(web, "run", yt_dlp_saves(tmp_path, [], name="other.webm", output=b""))
    with pytest.raises(AppError) as skipped:
        await web.fetch_set_audio(SET_URL, tmp_path)
    assert skipped.value.code == "DOWNLOAD_UNAVAILABLE" and "six hours" in skipped.value.message
    assert not (tmp_path / "receipt.json").exists()
    with pytest.raises(AppError) as unsupported:
        await web.fetch_set_audio("https://example.com/set", tmp_path)
    assert unsupported.value.code == "SOURCE_UNSUPPORTED"


async def test_size_guard_takes_a_larger_bound_for_sets(tmp_path):
    (tmp_path / "set.webm.part").write_bytes(b"x" * 20)
    sleeper = [sys.executable, "-c", "import time; time.sleep(10)"]
    with pytest.raises(AppError) as error:
        await web.run(sleeper, timeout=5, output_limit=100, download_dir=tmp_path, max_bytes=10)
    assert error.value.code == "DOWNLOAD_SIZE_LIMIT"
    # The default bound (500 MiB) is unchanged for single songs.
    output = await web.run(
        [sys.executable, "-c", "print('ok')"], timeout=5, output_limit=100, download_dir=tmp_path
    )
    assert output.strip() == b"ok"
