"""Set audio, windows and song recognition, without the network or a real Shazam."""

import io
import wave

import pytest

from djlib.audio import windows
from djlib.domain.errors import AppError


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
