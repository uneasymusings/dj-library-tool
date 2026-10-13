"""Set audio, windows and song recognition, without the network or a real Shazam."""

import base64
import hashlib
import hmac
import io
import json
import sys
import types
import wave
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from djlib.audio import windows
from djlib.domain.errors import AppError
from djlib.sources import recognition, web
from djlib.sources.recognition import Match, Probe

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
        # Like yt-dlp: a file already there is kept (and reported as saved), never replaced.
        if not (tmp_path / name).exists():
            (tmp_path / name).write_bytes(args[-1].encode())
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
    assert not (tmp_path / "fetching.json").exists()
    (args, options) = seen[0]
    assert args[args.index("--format") + 1] == "bestaudio/best"
    assert "--extract-audio" not in args and "--audio-format" not in args
    assert args[args.index("--max-filesize") + 1] == "2G"
    assert args[args.index("--match-filters") + 1] == (
        "duration <= 21600 & !is_live & !playlist_id"
    )
    assert args[args.index("--playlist-items") + 1] == "1"
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
    other = "https://soundcloud.com/keinemusik/live"
    (tmp_path / "set.m4a.part").write_bytes(b"half of the first set")
    await web.fetch_set_audio(other, tmp_path)
    assert (tmp_path / "set.webm").read_bytes() == other.encode()
    assert not (tmp_path / "set.m4a.part").exists()
    (tmp_path / "set.webm").unlink()
    await web.fetch_set_audio(other, tmp_path)
    (tmp_path / "receipt.json").write_text("broken")
    await web.fetch_set_audio(other, tmp_path)
    assert len(seen) == 4
    assert (tmp_path / "set.webm").read_bytes() == other.encode()


async def test_set_audio_resumes_only_its_own_interrupted_download(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "command", lambda: ["yt-dlp"])

    async def cancelled(args, **options):
        (tmp_path / "set.webm.part").write_bytes(b"first half")
        raise AppError("SOURCE_TIMEOUT", "Source retrieval timed out.", 504, True)

    monkeypatch.setattr(web, "run", cancelled)
    with pytest.raises(AppError):
        await web.fetch_set_audio(SET_URL, tmp_path)
    kept = []

    async def resumed(args, **options):
        kept.append(sorted(p.name for p in tmp_path.glob("set.*")))
        return b""

    monkeypatch.setattr(web, "run", resumed)
    with pytest.raises(AppError):
        await web.fetch_set_audio(SET_URL, tmp_path)
    with pytest.raises(AppError):
        await web.fetch_set_audio("https://soundcloud.com/keinemusik/live", tmp_path)
    # The same set picks up its part file; another set starts clean.
    assert kept == [["set.webm.part"], []]


async def test_set_audio_skipped_by_the_limits_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "command", lambda: ["yt-dlp"])
    # yt-dlp prints nothing when a filter (live, over six hours, over 2 GB) skips the set.
    monkeypatch.setattr(web, "run", yt_dlp_saves(tmp_path, [], name="other.webm", output=b""))
    with pytest.raises(AppError) as skipped:
        await web.fetch_set_audio(SET_URL, tmp_path)
    assert skipped.value.code == "DOWNLOAD_UNAVAILABLE" and "six hours" in skipped.value.message
    assert "playlist" in skipped.value.message
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


def test_size_guard_skips_files_that_vanish_while_it_counts(tmp_path, monkeypatch):
    (tmp_path / "set.webm.part").write_bytes(b"x" * 20)
    (tmp_path / "parts").mkdir()
    real = web.os.scandir

    class Gone:
        name = "set.webm.part-Frag7"

        def is_file(self):
            return True

        def stat(self):
            raise FileNotFoundError(self.name)

    class Listing:
        def __init__(self, folder):
            self.entries = [*real(folder), Gone()]

        def __enter__(self):
            return iter(self.entries)

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(web.os, "scandir", Listing)
    assert web._folder_bytes(tmp_path) == 20


class Clock:
    """A fake monotonic clock whose sleeps pass instantly."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def shazamio(monkeypatch):
    """A stand-in shazamio module: replies are dicts, exceptions, or "http" to use the client."""
    replies = []

    class Shazam:
        def __init__(self, http_client=None, **_):
            self.http_client = http_client

        async def recognize(self, data, proxy=None, options=None):
            assert isinstance(data, bytes)
            reply = replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            if reply == "http":
                return await self.http_client.request(
                    "POST",
                    "https://amp.shazam.com/discovery/v5/en-US/GB/web/-/tag/A/B?sync=true",
                    headers={"X-Shazam-Platform": "IPHONE"},
                    proxy=proxy,
                    json={"signature": {"uri": "data:audio/vnd.shazam.sig;base64,AA"}},
                )
            return reply

    module = types.ModuleType("shazamio")
    module.Shazam = Shazam
    monkeypatch.setitem(sys.modules, "shazamio", module)
    return replies


SHAZAM_REPLY = {
    "matches": [{"id": "1", "offset": 83.4, "timeskew": 0.031, "frequencyskew": 0.029}],
    "track": {
        "key": "692620240",
        "title": "Corpo e Canção (Club Mix)",
        "subtitle": "Antdot, Maz & Letícia Fialho",
        "isrc": "BRXYZ2400001",
        "url": "https://www.shazam.com/track/692620240/corpo-e-cancao",
    },
}


async def test_shazam_reply_becomes_a_match(shazamio):
    shazamio.extend(
        [
            SHAZAM_REPLY,
            {"matches": []},
            {"track": {"key": 715194703, "title": "Mirage", "subtitle": "Antdot"}, "matches": "?"},
            {"matches": [{"frequencyskew": -0.05}], "track": {"key": "9", "url": "http://x"}},
        ]
    )
    shazam = recognition.ShazamRecognizer(environ={"DJLIB_SHAZAM_INTERVAL": "0"})
    assert shazam.name == "shazam" and shazam.licensed is False
    assert await shazam.recognize(b"wav") == Match(
        "shazam",
        "692620240",
        "Antdot, Maz & Letícia Fialho",
        "Corpo e Canção (Club Mix)",
        "BRXYZ2400001",
        83.4,
        0.031,
        "https://www.shazam.com/track/692620240/corpo-e-cancao",
    )
    assert await shazam.recognize(b"wav") is None
    mirage = await shazam.recognize(b"wav")
    assert (mirage.track_id, mirage.artist, mirage.song_offset, mirage.isrc) == (
        "715194703",
        "Antdot",
        None,
        None,
    )
    # A pitch-shifted mix is still a match; only https links are kept.
    pitched = await shazam.recognize(b"wav")
    assert pitched.skew == -0.05 and pitched.url is None


async def test_shazam_requests_are_spaced_out(shazamio):
    shazamio.extend([{"matches": []}] * 3)
    clock = Clock()
    shazam = recognition.ShazamRecognizer(
        environ={"DJLIB_SHAZAM_INTERVAL": "2.5"}, clock=clock, sleep=clock.sleep
    )
    for _ in range(3):
        await shazam.recognize(b"wav")
    assert clock.slept == [2.5, 2.5]
    default = recognition.ShazamRecognizer(environ={"DJLIB_SHAZAM_INTERVAL": "soon"})
    assert default.pace.interval == 1.0


class Throttled(Exception):
    status = 429


async def test_shazam_backs_off_and_carries_the_back_off_over(shazamio):
    shazamio.extend(
        [Throttled(), Throttled(), SHAZAM_REPLY, Exception("429 Too Many Requests"), SHAZAM_REPLY]
    )
    clock = Clock()
    shazam = recognition.ShazamRecognizer(
        environ={"DJLIB_SHAZAM_INTERVAL": "1"}, clock=clock, sleep=clock.sleep
    )
    assert (await shazam.recognize(b"wav")).track_id == "692620240"
    assert clock.slept == [10.0, 20.0]
    # The next push-back starts from where the last one eased off to, not from scratch.
    clock.now += 60
    assert (await shazam.recognize(b"wav")).track_id == "692620240"
    assert clock.slept == [10.0, 20.0, 20.0]


async def test_shazam_gives_up_after_about_fifteen_minutes_of_back_off(shazamio):
    shazamio.extend([Throttled()] * 20)
    clock = Clock()
    shazam = recognition.ShazamRecognizer(
        environ={"DJLIB_SHAZAM_INTERVAL": "1"}, clock=clock, sleep=clock.sleep
    )
    with pytest.raises(AppError) as error:
        await shazam.recognize(b"wav")
    assert error.value.code == "RECOGNITION_RATE_LIMITED" and error.value.retryable
    assert clock.slept == [10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]
    assert 840 <= sum(clock.slept) <= 960


async def test_shazam_http_status_reaches_the_pacing(shazamio):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=SHAZAM_REPLY)

    shazamio.extend(["http", "http"])
    clock = Clock()
    shazam = recognition.ShazamRecognizer(
        environ={}, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep
    )
    assert (await shazam.recognize(b"wav")).title == "Corpo e Canção (Club Mix)"
    assert clock.slept == [10.0] and len(seen) == 2
    assert seen[1].headers["X-Shazam-Platform"] == "IPHONE"
    assert json.loads(seen[1].content)["signature"]["uri"].startswith("data:")


async def test_shazam_failures_are_window_errors(shazamio):
    shazamio.append(ValueError("bad signature"))
    shazam = recognition.ShazamRecognizer(environ={})
    with pytest.raises(AppError) as error:
        await shazam.recognize(b"wav")
    assert error.value.code == "RECOGNITION_FAILED" and error.value.retryable


def test_recognizer_choice_and_configuration(shazamio, monkeypatch):
    assert recognition.recognizer(environ={}).name == "shazam"
    assert recognition.recognizer(environ={"DJLIB_RECOGNIZER": " Shazam "}).name == "shazam"
    with pytest.raises(AppError) as audd:
        recognition.recognizer("audd", environ={})
    assert audd.value.code == "RECOGNIZER_NOT_CONFIGURED"
    assert "DJLIB_AUDD_TOKEN" in audd.value.message
    chosen = recognition.recognizer(environ={"DJLIB_RECOGNIZER": "audd", "DJLIB_AUDD_TOKEN": "t"})
    assert (chosen.name, chosen.licensed) == ("audd", True)
    with pytest.raises(AppError) as acr:
        recognition.recognizer("acrcloud", environ={"DJLIB_ACRCLOUD_HOST": "h.acrcloud.com"})
    assert acr.value.code == "RECOGNIZER_NOT_CONFIGURED"
    assert "DJLIB_ACRCLOUD_KEY, DJLIB_ACRCLOUD_SECRET" in acr.value.message
    acrcloud = {
        "DJLIB_ACRCLOUD_HOST": "https://identify-eu-west-1.acrcloud.com/",
        "DJLIB_ACRCLOUD_KEY": "k",
        "DJLIB_ACRCLOUD_SECRET": "s",
    }
    chosen = recognition.recognizer("acrcloud", environ=acrcloud)
    assert (chosen.name, chosen.host) == ("acrcloud", "identify-eu-west-1.acrcloud.com")
    with pytest.raises(AppError) as host:
        recognition.recognizer(
            "acrcloud", environ={**acrcloud, "DJLIB_ACRCLOUD_HOST": "evil.test/x?"}
        )
    assert host.value.code == "RECOGNIZER_NOT_CONFIGURED"
    with pytest.raises(AppError) as unknown:
        recognition.recognizer("midomi", environ={})
    assert unknown.value.code == "INPUT_INVALID"
    monkeypatch.setitem(sys.modules, "shazamio", None)
    with pytest.raises(AppError) as missing:
        recognition.recognizer(environ={})
    assert missing.value.code == "DEPENDENCY_REQUIRED"
    assert "installer" in missing.value.message and "recognize" in missing.value.message


def form(request: httpx.Request) -> dict:
    head = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=HTTP).parsebytes(head + request.content)
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
    }


AUDD_SONG = {
    "artist": "Fahlberg",
    "title": "Bad Love (Antdot & Maz Remix)",
    "timecode": "02:32",
    "song_link": "https://lis.tn/BadLove",
    "apple_music": {"isrc": "DEXYZ2300001"},
}


async def test_audd_window_recognition(monkeypatch):
    replies = [
        httpx.Response(429),
        httpx.Response(200, json={"status": "success", "result": AUDD_SONG}),
        httpx.Response(200, json={"status": "success", "result": None}),
        httpx.Response(200, json={"status": "error", "error": {"error_code": 900}}),
        httpx.Response(200, json={"status": "error", "error": {"error_code": 300}}),
        httpx.Response(502, text="gateway"),
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return replies.pop(0)

    clock = Clock()
    audd = recognition.AuddRecognizer(
        "token", transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep
    )
    assert await audd.recognize(b"RIFF") == Match(
        "audd",
        "fahlberg - bad love (antdot & maz remix)",
        "Fahlberg",
        "Bad Love (Antdot & Maz Remix)",
        "DEXYZ2300001",
        152.0,
        None,
        "https://lis.tn/BadLove",
    )
    assert clock.slept == [10.0]
    assert str(seen[1].url) == "https://api.audd.io/"
    fields = form(seen[1])
    assert fields["api_token"] == b"token" and fields["file"] == b"RIFF"
    assert await audd.recognize(b"RIFF") is None
    for code in ("RECOGNIZER_REJECTED", "RECOGNITION_FAILED", "RECOGNITION_FAILED"):
        with pytest.raises(AppError) as error:
            await audd.recognize(b"RIFF")
        assert error.value.code == code


async def test_audd_scans_a_whole_set_in_one_upload(tmp_path, monkeypatch):
    path = tmp_path / "set.webm"
    path.write_bytes(b"opus audio")
    monkeypatch.setattr(recognition, "duration", lambda _: 120.0)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "result": [
                    {"offset": "00:00", "songs": [AUDD_SONG]},
                    {
                        "offset": "00:48",
                        "songs": [
                            {"artist": "Antdot", "title": "Mirage", "score": 60},
                            {"artist": "Stif Mayer", "title": "Antdot Mirage", "score": 95},
                        ],
                    },
                    {"offset": "1:02:03", "songs": [{"artist": "Late", "title": "Extra"}]},
                    {"offset": "soon", "songs": [AUDD_SONG]},
                ],
            },
        )

    progress = []
    audd = recognition.AuddRecognizer("token", transport=httpx.MockTransport(handler))
    probes = await recognition.scan(
        path, audd, on_probe=lambda probe, done, total: progress.append((probe.at, done, total))
    )
    assert str(seen[0].url) == "https://enterprise.audd.io/"
    fields = form(seen[0])
    assert (fields["skip"], fields["every"], fields["accurate_offsets"]) == (b"1", b"1", b"true")
    assert fields["file"] == b"opus audio" and fields["api_token"] == b"token"
    assert [(p.at, p.window, p.match and p.match.title) for p in probes] == [
        (0.0, 12.0, "Bad Love (Antdot & Maz Remix)"),
        (24.0, 12.0, None),
        (48.0, 12.0, "Antdot Mirage"),
        (72.0, 12.0, None),
        (96.0, 12.0, None),
        (3723.0, 12.0, "Extra"),
    ]
    assert progress[-1] == (3723.0, 6, 6)
    assert await audd.scan_file(path, should_stop=lambda: True) == []


async def test_acrcloud_signs_each_request(monkeypatch):
    replies = [
        {
            "status": {"code": 0, "msg": "Success"},
            "metadata": {
                "music": [
                    {
                        "acrid": "a1b2",
                        "title": "Stay",
                        "artists": [{"name": "Antdot"}, {"name": "Mozambo"}, {}],
                        "external_ids": {"isrc": ["GBXYZ2200001"]},
                        "play_offset_ms": 61200,
                    }
                ]
            },
        },
        {"status": {"code": 1001, "msg": "No result"}},
        {"status": {"code": 3015, "msg": "QpS limit exceeded"}},
        {"status": {"code": 2004, "msg": "Can't generate fingerprint"}},
        {"status": {"code": 3014, "msg": "Invalid signature"}},
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=replies.pop(0))

    clock = Clock()
    acr = recognition.AcrCloudRecognizer(
        "identify-eu-west-1.acrcloud.com",
        "key",
        "secret",
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
        wall=lambda: 1700000000.9,
    )
    assert await acr.recognize(b"RIFFwav") == Match(
        "acrcloud", "a1b2", "Antdot, Mozambo", "Stay", "GBXYZ2200001", 61.2, None, None
    )
    assert str(seen[0].url) == "https://identify-eu-west-1.acrcloud.com/v1/identify"
    fields = form(seen[0])
    expected = base64.b64encode(
        hmac.new(b"secret", b"POST\n/v1/identify\nkey\naudio\n1\n1700000000", hashlib.sha1).digest()
    )
    assert fields == {
        "access_key": b"key",
        "sample_bytes": b"7",
        "timestamp": b"1700000000",
        "signature": expected,
        "data_type": b"audio",
        "signature_version": b"1",
        "sample": b"RIFFwav",
    }
    assert await acr.recognize(b"RIFFwav") is None
    # A requests-per-second limit backs off, then the window is asked again.
    assert await acr.recognize(b"RIFFwav") is None
    assert clock.slept == [10.0]
    with pytest.raises(AppError) as rejected:
        await acr.recognize(b"RIFFwav")
    assert rejected.value.code == "RECOGNIZER_REJECTED"


class Timeline:
    """A fake recognizer that hears whichever song plays at the window's start."""

    name = "timeline"
    licensed = False

    def __init__(self, songs, failures=()):
        self.songs, self.failures, self.heard = songs, dict(failures), []

    async def recognize(self, wav):
        at = float(wav.decode())
        self.heard.append(at)
        if at in self.failures:
            raise self.failures[at]
        for start, end, title in self.songs:
            if start <= at < end:
                return Match("timeline", title, "Artist", title)
        return None


@pytest.fixture
def set_audio(monkeypatch, tmp_path):
    """A 100-second set whose windows carry their own start time instead of audio."""
    monkeypatch.setattr(recognition, "duration", lambda _: 100.0)
    monkeypatch.setattr(recognition, "window_wav", lambda path, start, length: str(start).encode())
    return tmp_path / "set.webm"


def test_window_placement_fits_inside_the_set():
    assert recognition.placements(100.0) == [9.0, 39.0, 69.0]
    assert recognition.placements(51.0) == [9.0, 39.0]
    assert recognition.placements(20.0) == []
    assert recognition.placements(30.0, window=10, stride=7.5, offset=0) == [0.0, 7.5, 15.0]
    with pytest.raises(AppError):
        recognition.placements(100.0, stride=0)


async def test_scan_reports_progress_keeps_failed_windows_and_stops_early(set_audio):
    timeline = Timeline(
        [(0, 50, "A"), (50, 80, "B")],
        failures={39.0: AppError("RECOGNITION_FAILED", "Shazam did not answer.", 502, True)},
    )
    progress = []

    async def on_probe(probe, done, total):
        progress.append((probe.at, done, total))

    probes = await recognition.scan(set_audio, timeline, on_probe=on_probe)
    assert [p.as_dict() for p in probes] == [
        {
            "at": 9.0,
            "window": 12.0,
            "match": {
                "provider": "timeline",
                "track_id": "A",
                "artist": "Artist",
                "title": "A",
                "isrc": None,
                "song_offset": None,
                "skew": None,
                "url": None,
            },
            "error": None,
        },
        {"at": 39.0, "window": 12.0, "match": None, "error": "Shazam did not answer."},
        {**probes[2].as_dict(), "at": 69.0},
    ]
    assert probes[2].match.title == "B"
    assert progress == [(9.0, 1, 3), (39.0, 2, 3), (69.0, 3, 3)]
    assert [Probe.from_dict(json.loads(json.dumps(p.as_dict()))) for p in probes] == probes
    assert await recognition.scan(set_audio, Timeline([]), should_stop=lambda: True) == []
    seen = []

    def stop():
        return len(seen) >= 2

    early = await recognition.scan(
        set_audio, Timeline([]), on_probe=lambda probe, *_: seen.append(probe), should_stop=stop
    )
    assert [p.at for p in early] == [9.0, 39.0]


async def test_scan_ends_on_errors_that_would_repeat(set_audio, monkeypatch):
    limited = AppError("RECOGNITION_RATE_LIMITED", "Slow down.", 502, True)
    with pytest.raises(AppError) as error:
        await recognition.scan(set_audio, Timeline([], failures={39.0: limited}))
    assert error.value is limited

    def broken(path, start, length):
        raise AppError("AUDIO_INVALID", "FFmpeg could not decode this part of the recording.")

    monkeypatch.setattr(recognition, "window_wav", broken)
    probes = await recognition.scan(set_audio, Timeline([]))
    assert {p.error for p in probes} == {"FFmpeg could not decode this part of the recording."}


async def test_scan_reads_real_audio_windows(audio_factory):
    path = audio_factory("set.wav", frames=44100 * 32)
    heard = []

    class Listener:
        name, licensed = "listener", False

        async def recognize(self, wav):
            heard.append(frames(wav))
            return None

    probes = await recognition.scan(path, Listener(), stride=10.0)
    assert [(p.at, p.match, p.error) for p in probes] == [(9.0, None, None), (19.0, None, None)]
    for channels, width, rate, count in heard:
        assert (channels, width, rate) == (1, 2, 16000) and 191000 <= count <= 192000
    assert len(heard) == 2


def probes_of(timeline, positions):
    return [
        Probe(
            at,
            12.0,
            next((Match("timeline", t, "Artist", t) for s, e, t in timeline if s <= at < e), None),
        )
        for at in positions
    ]


async def test_refine_probes_between_disagreeing_neighbours(set_audio):
    songs = [(0, 50, "A"), (50, 80, "B")]
    first = probes_of(songs, [90.0, 0.0, 30.0, 60.0])
    timeline = Timeline(songs)
    progress = []
    found = await recognition.refine(
        set_audio,
        timeline,
        first,
        min_spacing=5.0,
        on_probe=lambda probe, done, total: progress.append((probe.at, done, total)),
    )
    # A|B between 30 and 60, B|silence between 60 and 90; then one level closer.
    assert timeline.heard == [45.0, 75.0, 52.5, 82.5]
    assert [p.at for p in found] == [0.0, 30.0, 45.0, 52.5, 60.0, 75.0, 82.5, 90.0]
    assert [p.match and p.match.title for p in found] == ["A", "A", "A", "B", "B", "B", None, None]
    assert progress == [(45.0, 1, 2), (75.0, 2, 2), (52.5, 3, 4), (82.5, 4, 4)]


async def test_refine_respects_spacing_limit_and_stop(set_audio):
    songs = [(0, 50, "A"), (50, 80, "B")]
    first = probes_of(songs, [0.0, 30.0, 60.0, 90.0])
    default = Timeline(songs)
    found = await recognition.refine(set_audio, default, first)
    assert default.heard == [45.0, 75.0]  # 15 s apart; another level would be under 10 s
    assert len(found) == 6
    capped = Timeline(songs)
    assert len(await recognition.refine(set_audio, capped, first, limit=1)) == 5
    assert capped.heard == [45.0]
    stopped = Timeline(songs)
    found = await recognition.refine(set_audio, stopped, first, should_stop=lambda: True)
    assert stopped.heard == [] and found == first
    silent = probes_of([], [0.0, 30.0, 60.0])
    assert await recognition.refine(set_audio, Timeline(songs), silent) == silent
