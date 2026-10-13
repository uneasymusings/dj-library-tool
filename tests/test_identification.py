"""Identifying a set from its link: the identify job, its folder, resuming and reuse.

Nothing here reaches YouTube, SoundCloud or Shazam: the page, the download and the
recognizer are fakes, and windows carry their start time instead of audio.
"""

import asyncio
import json
from pathlib import Path

import pytest

from djlib.application import identification
from djlib.application.identification import IdentifyRunner
from djlib.audio import windows
from djlib.domain.contracts import IdentifyRequest
from djlib.domain.errors import AppError
from djlib.jobs.worker import Worker
from djlib.sources import recognition, web
from djlib.sources.recognition import Match

URL = "https://www.youtube.com/watch?v=fridayset1"
DATA = Path(__file__).parent / "data"


class Listener:
    """A fake recognizer that hears whichever song plays at the window's start."""

    licensed = False

    def __init__(self, songs, name="shazam"):
        self.songs, self.name = songs, name
        self.failures: dict[float, Exception] = {}
        self.heard: list[float] = []
        self.hook = None

    async def recognize(self, wav):
        at = float(wav.decode())
        self.heard.append(at)
        if self.hook is not None:
            await self.hook(at)
        if at in self.failures:
            raise self.failures[at]
        for start, end, artist, title in self.songs:
            if start <= at < end:
                return Match(self.name, f"{artist} - {title}", artist, title)
        return None


class Setup:
    """The page, download and recognizer an identification sees."""

    def __init__(self, monkeypatch):
        self.seconds = 300.0
        self.page = {
            "provider": "Youtube",
            "source_id": "fridayset1",
            "title": "Friday Set",
            "uploader": "Lumen",
            "duration_seconds": 300,
            "description": "",
            "chapters": [],
            "comments": [],
        }
        self.inspected: list[str] = []
        self.fetched: list[str] = []
        self.made: list[str | None] = []
        self.listener = Listener([(0, 150, "Lumen", "Halo"), (150, 300, "Nia Okoro", "Slow Burn")])
        import djlib.interfaces.service as service_module

        # The identify job reads the page, and so does `djlib set` (POST /sources/inspect).
        monkeypatch.setattr(web, "inspect_source", self.inspect)
        monkeypatch.setattr(service_module, "inspect_source", self.inspect)
        monkeypatch.setattr(web, "fetch_set_audio", self.fetch)
        monkeypatch.setattr(recognition, "duration", lambda path: self.seconds)
        monkeypatch.setattr(windows, "duration", lambda path: self.seconds)
        monkeypatch.setattr(recognition, "window_wav", lambda path, at, length: str(at).encode())
        monkeypatch.setattr(identification, "recognizer", self.recognizer)
        monkeypatch.setattr(identification, "POLL_SECONDS", 0.01)
        monkeypatch.setattr(identification.shutil, "which", lambda name: f"/usr/bin/{name}")

    async def inspect(self, url, comments=0):
        assert comments == identification.COMMENTS
        self.inspected.append(url)
        return {**self.page, "url": url}

    async def fetch(self, url, destination):
        self.fetched.append(url)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "set.webm"
        path.write_bytes(b"set audio")
        return {"url": url, "duration_seconds": self.page["duration_seconds"], "path": str(path)}

    def recognizer(self, name=None):
        self.made.append(name)
        return self.listener


@pytest.fixture
def setup(monkeypatch):
    return Setup(monkeypatch)


KEYS = iter(range(1_000_000))


def submit(application, **request) -> str:
    body = IdentifyRequest(url=request.pop("url", URL), idempotency_key=f"k{next(KEYS)}", **request)
    return identification.submit(application, body)["job_id"]


async def run(application, job_id=None) -> dict:
    runner = IdentifyRunner(application)
    claimed = runner.claim()
    assert claimed is not None and (job_id is None or claimed[0] == job_id)
    await runner.execute(*claimed)
    return application.job(claimed[0])


def folder(application, url=URL) -> Path:
    return identification.set_folder(application.workspace, url)


def probes(application) -> list[dict]:
    return identification.load_probes(folder(application))


def phases(application, job_id) -> list[str]:
    events = application.events(job_id, limit=100)["events"]
    return [event["data"]["phase"] for event in events if event["kind"] == "phase"]


# -- links -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "canonical"),
    [
        ("https://youtu.be/fridayset1?t=120", URL),
        ("https://m.youtube.com/watch?v=fridayset1&list=PL1&index=3", URL),
        ("http://www.youtube.com/live/fridayset1", URL),
        (
            "https://soundcloud.com/lumen/friday-set/?utm_source=clipboard&si=1",
            "https://soundcloud.com/lumen/friday-set",
        ),
        ("https://m.soundcloud.com/lumen/friday-set", "https://soundcloud.com/lumen/friday-set"),
    ],
)
def test_every_link_to_a_set_shares_one_folder(link, canonical):
    assert identification.canonical_url(link) == canonical
    assert identification.set_key(link) == identification.set_key(canonical)


@pytest.mark.parametrize(
    ("link", "code"),
    [
        ("https://www.1001tracklists.com/tracklist/abc/set.html", "SOURCE_BROWSER_ONLY"),
        ("https://www.mixcloud.com/lumen/set/", "SOURCE_UNSUPPORTED"),
        ("https://www.youtube.com/playlist?list=PL1", "SINGLE_SOURCE_REQUIRED"),
        ("https://soundcloud.com/", "SINGLE_SOURCE_REQUIRED"),
        ("not a link", "SOURCE_UNSUPPORTED"),
    ],
)
def test_other_links_are_refused_with_a_way_forward(link, code):
    with pytest.raises(AppError) as refused:
        identification.canonical_url(link)
    assert refused.value.code == code
    if code == "SOURCE_BROWSER_ONLY":
        assert "--tracklist FILE" in refused.value.message


def test_submitting_needs_song_recognition_unless_the_set_was_identified(application):
    # conftest: recognition is not installed.
    with pytest.raises(AppError) as missing:
        submit(application)
    assert missing.value.code == "DEPENDENCY_REQUIRED"
    assert "--python 3.12" in missing.value.message
    saved = folder(application)
    saved.mkdir(parents=True)
    (saved / "tracklist.json").write_text(json.dumps({"complete": True, "tracklist": {}}))
    assert application.job(submit(application))["state"] == "queued"  # reused: nothing to hear
    with pytest.raises(AppError):
        submit(application, again=True)


def test_the_worker_leaves_identifications_to_their_own_runner(application, setup):
    job_id = submit(application)
    assert Worker(application)._claim_next() is None
    assert application.busy()  # idle shutdown still waits for it
    assert IdentifyRunner(application).claim()[0] == job_id


# -- one identification ------------------------------------------------------------------------


async def test_an_identification_reads_listens_checks_gaps_and_merges(application, setup):
    setup.page["description"] = "Tracklist:\nLumen - Halo\nNia Okoro - Slow Burn (Dub)\n"
    job_id = submit(application, url="https://youtu.be/fridayset1")
    job = await run(application, job_id)

    assert job["state"] == "completed" and job["outcome"] == "complete"
    assert phases(application, job_id) == [
        "reading_page",
        "downloading",
        "listening",
        "checking_gaps",
        "merging",
    ]
    result = job["result"]
    assert result["source"] == {
        "url": URL,
        "provider": "Youtube",
        "title": "Friday Set",
        "uploader": "Lumen",
        "duration_seconds": 300.0,
    }
    assert result["recognizer"] == {"name": "shazam", "licensed": False}
    assert result["counts"] == {
        "confirmed": 2,
        "likely": 0,
        "weak": 0,
        "conflict": 0,
        "unidentified": 0,
    }
    halo, burn = result["tracklist"]["tracks"]
    assert (halo["start"], halo["title"], halo["status"]) == (9.0, "Halo", "confirmed")
    assert (burn["start"], burn["version"], burn["status"]) == (159.0, "Dub", "confirmed")
    assert result["reused"] is False and "progress" not in result

    # One window every 30 seconds from 0:09, then one between the two songs.
    heard = probes(application)
    assert [p["at"] for p in heard if p["pass"] == "scan"] == [9.0 + 30 * n for n in range(10)]
    assert [p["at"] for p in heard if p["pass"] == "refine"] == [144.0]
    assert {p["recognizer"] for p in heard} == {"shazam"}
    saved = folder(application)
    assert not (saved / "audio").exists()  # the audio goes; what was heard stays
    text = (saved / "tracklist.txt").read_text(encoding="utf-8").splitlines()
    assert text == ["Friday Set", "[00:09] Lumen - Halo", "[02:39] Nia Okoro - Slow Burn (Dub)"]
    assert result["tracklist_file"] == str(saved / "tracklist.txt")
    assert json.loads((saved / "tracklist.json").read_text())["tracklist"] == result["tracklist"]
    assert json.loads((saved / "page.json").read_text())["title"] == "Friday Set"


async def test_the_audio_can_be_kept_and_a_page_just_read_is_not_read_again(
    application, setup, monkeypatch
):
    monkeypatch.setenv("DJLIB_KEEP_SET_AUDIO", "1")
    # `djlib set` read the page with its comments a moment ago (POST /sources/inspect).
    identification.remember_page({**setup.page, "url": "https://youtu.be/fridayset1"}, 1000)
    assert (await run(application, submit(application)))["state"] == "completed"
    assert setup.inspected == []
    assert (folder(application) / "audio" / "set.webm").is_file()


async def test_progress_counts_windows_and_songs(application, setup):
    seen = []
    job_id = submit(application)

    async def look(at):
        seen.append(application.job(job_id)["result"]["progress"])

    setup.listener.hook = look
    await run(application, job_id)
    listening = [p for p in seen if p["phase"] == "listening"]
    assert [p["windows_done"] for p in listening] == list(range(10))
    assert {p["windows_total"] for p in listening} == {10}
    assert listening[-1]["songs_heard"] == 2 and listening[-1]["eta_seconds"] is not None
    assert seen[-1]["phase"] == "checking_gaps" and seen[-1]["windows_total"] == 5


# -- stopping, resuming, failing ---------------------------------------------------------------


async def test_a_restarted_service_resumes_without_asking_for_heard_windows_again(
    application, setup
):
    job_id = submit(application)

    async def restart(at):
        if at == 99.0:
            Worker(application).recover()  # what a restarted service does first
            await asyncio.sleep(1)

    setup.listener.hook = restart
    first = await run(application, job_id)
    assert first["state"] == "queued"
    assert [p["at"] for p in probes(application)] == [9.0, 39.0, 69.0]
    assert (folder(application) / "audio").exists()  # kept for the rest of the set

    setup.listener.hook = None
    done = await run(application, job_id)
    assert done["state"] == "completed"
    assert setup.listener.heard[:5] == [9.0, 39.0, 69.0, 99.0, 99.0]
    scanned = [p["at"] for p in probes(application) if p["pass"] == "scan"]
    assert scanned == [9.0 + 30 * n for n in range(10)]  # each window once
    assert setup.fetched == [URL, URL]


async def test_cancelling_stops_listening_at_once(application, setup):
    job_id = submit(application)
    forever = asyncio.Event()

    async def hang(at):
        if at == 39.0:
            application.control(job_id, "cancel")
            await forever.wait()

    setup.listener.hook = hang
    job = await asyncio.wait_for(run(application, job_id), timeout=5)
    assert job["state"] == "cancelled"
    assert [p["at"] for p in probes(application)] == [9.0]  # kept for the next try
    assert not (folder(application) / "audio").exists()


async def test_pausing_keeps_the_audio_and_resume_continues(application, setup):
    job_id = submit(application)

    async def pause(at):
        if at == 39.0:
            application.control(job_id, "pause")
            await asyncio.sleep(1)

    setup.listener.hook = pause
    assert (await run(application, job_id))["state"] == "paused"
    assert (folder(application) / "audio").exists()
    setup.listener.hook = None
    application.control(job_id, "resume")
    assert (await run(application, job_id))["state"] == "completed"
    assert setup.listener.heard.count(9.0) == 1


async def test_a_rate_limit_keeps_what_was_heard_and_a_retry_resumes(application, setup):
    limited = AppError(
        "RECOGNITION_RATE_LIMITED",
        "Shazam is turning away requests from this computer for now; try the scan again later.",
        502,
        True,
    )
    setup.listener.failures[69.0] = limited
    job_id = submit(application)
    failed = await run(application, job_id)
    assert failed["state"] == "failed"
    error = failed["result"]["error"]
    assert error["code"] == "RECOGNITION_RATE_LIMITED" and error["retryable"] is True
    assert "(2 listening windows) is saved" in error["message"]
    assert error["details"] == {"windows_heard": 2, "resumes": True}
    assert failed["result"]["progress"]["phase"] == "listening"
    assert (folder(application) / "audio").exists()  # no second download for the retry

    del setup.listener.failures[69.0]
    application.control(job_id, "retry")
    done = await run(application, job_id)
    assert done["state"] == "completed" and "error" not in done["result"]
    assert setup.listener.heard[:4] == [9.0, 39.0, 69.0, 69.0]
    assert setup.listener.heard.count(39.0) == 1


async def test_a_soundcloud_preview_is_refused_in_plain_words(application, setup):
    setup.page["duration_seconds"] = 3600
    setup.seconds = 30.0
    job = await run(application, submit(application))
    error = job["result"]["error"]
    assert job["state"] == "failed" and error["code"] == "SET_PREVIEW_ONLY"
    assert "00:30 of this 1:00:00 set" in error["message"] and "Go+" in error["message"]
    assert "djlib set FILE --source " + URL in error["message"]
    assert setup.listener.heard == [] and not (folder(application) / "audio").exists()


async def test_unconfigured_licensed_recognizers_say_to_restart_the_service(
    application, setup, monkeypatch
):
    job_id = submit(application)

    def unconfigured(name=None):
        raise AppError(
            "RECOGNIZER_NOT_CONFIGURED", "AudD needs an API token: set DJLIB_AUDD_TOKEN."
        )

    # The service's settings changed between submitting and listening.
    monkeypatch.setattr(identification, "recognizer", unconfigured)
    job = await run(application, job_id)
    message = job["result"]["error"]["message"]
    assert message.startswith("AudD needs an API token") and "djlib service stop" in message


# -- reuse ---------------------------------------------------------------------------------------


async def test_a_set_identified_before_is_reused_and_new_tracklists_merge(application, setup):
    await run(application, submit(application))
    asked, made = list(setup.listener.heard), len(setup.made)

    reused = (await run(application, submit(application)))["result"]
    assert reused["reused"] is True
    assert setup.fetched == [URL] and setup.inspected == [URL] and len(setup.made) == made
    assert setup.listener.heard == asked

    listing = "1. Lumen - Halo\n2. Nia Okoro - Slow Burn\n3. Bicep - Glue\n"
    merged = (
        await run(
            application,
            submit(application, tracklists=[{"source": "1001tracklists", "text": listing}]),
        )
    )["result"]
    assert [s["source"] for s in merged["tracklist"]["sources"]] == ["audio", "1001tracklists"]
    assert [t["title"] for t in merged["tracklist"]["tracks"]] == ["Halo", "Slow Burn", "Glue"]
    # The tracklist is remembered for the set: later reuse keeps it.
    later = (await run(application, submit(application)))["result"]
    assert [t["title"] for t in later["tracklist"]["tracks"]] == ["Halo", "Slow Burn", "Glue"]
    assert setup.listener.heard == asked

    again = (await run(application, submit(application, again=True)))["result"]
    assert again["reused"] is False and setup.fetched == [URL, URL]
    assert len(setup.listener.heard) == 2 * len(asked)
    assert len(probes(application)) == len(asked)  # heard afresh, not added twice


async def test_the_unofficial_shazam_notice_comes_once_per_workspace(application, setup):
    first = (await run(application, submit(application)))["result"]
    assert first["notices"] == [identification.UNOFFICIAL_SHAZAM]
    assert "DJLIB_AUDD_TOKEN" in first["notices"][0] and "$" not in first["notices"][0]
    second = (await run(application, submit(application, again=True)))["result"]
    assert second["notices"] == []


async def test_licensed_recognizers_bring_no_notice(application, setup):
    setup.listener.name, setup.listener.licensed = "audd", True
    result = (await run(application, submit(application, recognizer="audd")))["result"]
    assert result["recognizer"] == {"name": "audd", "licensed": True}
    assert result["notices"] == [] and set(setup.made) == {"audd"}
    shazam = (await run(application, submit(application, recognizer="shazam")))["result"]
    assert shazam["reused"] is True
    assert "identified with AudD before" in shazam["notices"][0]


# -- the Keinemusik set --------------------------------------------------------------------------


def keinemusik_listener() -> Listener:
    rows = [json.loads(line) for line in (DATA / "keinemusik-scan.jsonl").read_text().splitlines()]
    listener = Listener([])

    async def recognize(wav):
        at = float(wav.decode())
        listener.heard.append(at)
        row = max((row for row in rows if row["t"] <= at), key=lambda row: row["t"])
        if not row["title"]:
            return None
        return Match("shazam", row["key"], row["artist"], row["title"])

    listener.recognize = recognize
    return listener


async def test_the_keinemusik_set_end_to_end(application, setup, monkeypatch):
    """The real 62-minute scan, heard window by window, merged with its description."""
    monkeypatch.setattr(identification, "OFFSET", 0.0)  # the scan's windows start at 0:00
    setup.seconds, setup.page["duration_seconds"] = 3720.0, 3720
    setup.page["title"] = "Keinemusik - Antdot & Maz at Cercle"
    setup.page["description"] = "Listen to Antdot & Maz: https://example.test/antdot\n" + (
        DATA / "keinemusik-description.txt"
    ).read_text(encoding="utf-8")
    setup.listener = keinemusik_listener()
    job = await run(application, submit(application))
    assert job["state"] == "completed"
    result = identification.summary(job["result"])

    tracks = {track["start"]: track for track in result["tracks"]}
    mirage = tracks["06:00"]
    assert (mirage["artist"], mirage["title"], mirage["status"]) == (
        "Antdot",
        "Mirage",
        "confirmed",
    )
    # Checking gaps listened between windows; those listens never count as windows.
    assert mirage["evidence"] == "Shazam: 11 windows from 06:00; also in the description"
    assert len([p for p in probes(application) if p["pass"] == "refine"]) > 10
    late_id = tracks["48:30"]
    assert (late_id["kind"], late_id["artist"], late_id["status"]) == ("unknown", None, "likely")
    top = late_id["candidates"][0]
    assert top["label"] == "Chris Fortier & Neil Kolo - All I Got (Chris Fortier 20yr Dub)"
    assert (top["source"], top["heard_by"], top["windows"], top["at"]) == (
        "audio",
        "Shazam",
        2,
        "50:30",
    )
    assert {song["label"] for song in result["unplaced"]} == {
        "Q-Force - Slacker (Pierre J Remix)",
        "BLR & Rave & Crave - Taj",
        "Paolo Rocco - People Say (Tech House Edit) (Nic Fanciulli Remix)",
    }
    assert late_id["evidence"].startswith("ID (likely Chris Fortier & Neil Kolo - All I Got")
    assert result["counts"]["tracks"] == 13 and result["counts"]["confirmed"] >= 8
    assert result["coverage"]["percent"] >= 80 and result["listed"] is True
    assert result["duration"] == "1:02:00"
    lines = Path(result["tracklist_file"]).read_text(encoding="utf-8").splitlines()
    # The title has an "Artist - Title" dash, yet `djlib set FILE` reads it as the heading.
    assert lines[:3] == [
        "Keinemusik - Antdot & Maz at Cercle",
        "[00:00] ID - ID",
        "[06:00] Antdot - Mirage",
    ]
    from djlib.application.tracklists import HEADING, parse_tracklist

    items, skipped = parse_tracklist("\n".join(lines))
    assert skipped == [(1, lines[0], HEADING)] and len(items) == 13


async def test_the_runner_survives_a_busy_catalog_and_runs_queued_identifications(
    application, setup, monkeypatch
):
    runner = IdentifyRunner(application)
    claim, calls = runner.claim, []

    def busy_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return claim()

    monkeypatch.setattr(runner, "claim", busy_once)
    job_id = submit(application)
    task = asyncio.create_task(runner.run())
    try:
        for _ in range(200):
            if application.job(job_id)["state"] == "completed":
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert application.job(job_id)["state"] == "completed" and len(calls) >= 2
