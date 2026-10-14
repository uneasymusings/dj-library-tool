"""Identifying a set from its link: the identify job, its folder, resuming and reuse.

Nothing here reaches YouTube, SoundCloud or Shazam: the page, the download and the
recognizer are fakes, and windows carry their start time instead of audio.
"""

import asyncio
import json
import time
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


class Cuts:
    """Stands in for ``recognition.SetAudio``: windows carry their start time, not audio."""

    def __init__(self, path, seconds=None):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def window(self, start, length=12.0):
        return str(start).encode()


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
        monkeypatch.setattr(recognition, "SetAudio", Cuts)
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
        ("https://1001.tl/14rk2xyz", "SOURCE_BROWSER_ONLY"),
        ("https://www.mixcloud.com/lumen/set/", "SOURCE_UNSUPPORTED"),
        ("https://www.youtube.com/playlist?list=PL1", "SINGLE_SOURCE_REQUIRED"),
        ("https://soundcloud.com/", "SINGLE_SOURCE_REQUIRED"),
        ("not a link", "SOURCE_UNSUPPORTED"),
    ],
)
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_other_links_are_refused_with_a_way_forward(monkeypatch, link, code, platform):
    monkeypatch.setattr(web.sys, "platform", platform)
    with pytest.raises(AppError) as refused:
        identification.canonical_url(link)
    assert refused.value.code == code
    copied = "paste" if platform == "darwin" else "FILE"  # `paste` reads a Mac's clipboard
    if code == "SOURCE_BROWSER_ONLY":
        # The copied tracklist with the set's own link, or checked against the music alone.
        details = refused.value.details
        assert details["next"] == f"djlib identify 'LINK' --tracklist {copied}"
        assert [step["command"] for step in details["next_steps"]] == [
            f"djlib identify 'LINK' --tracklist {copied}",
            f"djlib set {copied}",
        ]
        assert details["next_label"] == (
            "With the set's YouTube or SoundCloud link (under the player)"
        )
        assert details["next_steps"][0]["note"]  # the order, or what FILE is
        assert all(len(step["label"]) <= 70 for step in details["next_steps"])  # 80 columns
        assert "djlib identify 'LINK' --tracklist -" in details["next_for_assistants"]
    if "mixcloud" in link:
        assert "Mixcloud doesn't allow downloads" in refused.value.message
        assert f"djlib set {copied}" in refused.value.message


@pytest.mark.parametrize(
    ("sources", "asks"),
    [
        ([{"source": "audio"}], True),
        ([{"source": "audio"}, {"source": "description"}], True),
        ([{"source": "audio"}, {"source": "1001tracklists"}], False),
        ([{"source": "audio"}, {"source": "file"}], False),  # pasted, or a file
        ([{"source": "audio"}, {"source": "mixesdb"}], False),  # copied from MixesDB
        ([{"source": "audio"}, {"source": "mixesdb", "page_url": "https://mixesdb.com/w/x"}], True),
    ],
)
def test_a_tracklist_the_dj_gave_is_not_asked_for_again(sources, asks):
    """Only a tracklist djlib found itself leaves room to add one for the IDs left."""
    found = {
        "source": {"url": URL},
        "tracks": [{"kind": "unknown", "status": "unidentified"}],
        "coverage": {"percent": 90},
        "sources": sources,
    }
    asked = f"djlib identify '{URL}' --tracklist FILE"
    assert identification.next_steps(found) == [f"djlib set '{URL}'", *([asked] if asks else [])]


def test_submitting_needs_song_recognition_unless_the_set_was_identified(application):
    # conftest: recognition is not installed.
    with pytest.raises(AppError) as missing:
        submit(application)
    assert missing.value.code == "DEPENDENCY_REQUIRED"
    assert "installer" in missing.value.message
    assert missing.value.details["next"] == identification.INSTALLER
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


TIMED = "\n".join(f"[{2 * n:02d}:00] Artist {n} - Song {n}" for n in range(5))
TWO_MINUTE_SONGS = [(120 * n, 120 * (n + 1), f"Artist {n}", f"Song {n}") for n in range(5)]


async def test_a_tracklist_that_times_its_songs_halves_the_windows(application, setup):
    setup.seconds, setup.page["duration_seconds"] = 600.0, 600
    setup.page["description"] = TIMED
    setup.listener.songs = TWO_MINUTE_SONGS
    job = await run(application, submit(application))
    assert job["state"] == "completed"
    scanned = [p["at"] for p in probes(application) if p["pass"] == "scan"]
    assert scanned == [9.0 + 60 * n for n in range(10)]  # every minute, not every 30 s
    tracks = job["result"]["tracklist"]["tracks"]
    assert [(t["title"], t["status"]) for t in tracks] == [
        (f"Song {n}", "confirmed") for n in range(5)
    ]


@pytest.mark.parametrize(
    "description",
    [
        "",  # no tracklist
        "\n".join(TIMED.splitlines()[:4]),  # four songs are too few to steer by
        "\n".join(line.split("] ")[1] for line in TIMED.splitlines()),  # no times
    ],
)
async def test_without_a_timed_tracklist_windows_stay_30_seconds_apart(
    application, setup, description
):
    setup.page["description"] = description
    assert (await run(application, submit(application)))["state"] == "completed"
    scanned = [p["at"] for p in probes(application) if p["pass"] == "scan"]
    assert scanned == [9.0 + 30 * n for n in range(10)]


async def test_a_resumed_listen_keeps_its_spacing(application, setup):
    setup.seconds, setup.page["duration_seconds"] = 600.0, 600
    setup.page["description"] = TIMED
    job_id = submit(application)

    async def restart(at):
        if at == 129.0:
            Worker(application).recover()
            await asyncio.sleep(1)

    setup.listener.hook = restart
    assert (await run(application, job_id))["state"] == "queued"
    assert application.job(job_id)["result"]["stride"] == 60.0
    # The tracklist is gone by the time the job resumes; the job still listens every minute.
    setup.page["description"] = ""
    (folder(application) / "page.json").unlink()
    identification.PAGES.clear()
    setup.listener.hook = None
    assert (await run(application, job_id))["state"] == "completed"
    scanned = [p["at"] for p in probes(application) if p["pass"] == "scan"]
    assert scanned == [9.0 + 60 * n for n in range(10)]


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
    # How far it got in the set's own time: 30 seconds a window, in a five-minute set.
    assert [p["position_seconds"] for p in listening] == [30 * done for done in range(10)]
    assert {p["duration_seconds"] for p in listening} == {300}
    assert {p["waited_seconds"] for p in listening} == {0}
    assert seen[-1]["phase"] == "checking_gaps" and seen[-1]["windows_total"] == 5
    # Listening again where songs change jumps around the set: no position there.
    assert seen[-1]["position_seconds"] is None and seen[-1]["duration_seconds"] == 300


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
    # So is the audio: running the same command again continues without downloading again.
    assert (folder(application) / "audio").exists()
    setup.listener.hook = None
    done = await run(application, submit(application))
    assert done["state"] == "completed" and setup.fetched == [URL, URL]
    assert setup.listener.heard.count(9.0) == 1 and setup.inspected == [URL]


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
    assert "What djlib heard so far is saved" in error["message"]
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
    assert error["details"]["next"] == f"djlib set FILE --source '{URL}'"
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
    assert "djlib doctor" in first["notices"][0] and "$" not in first["notices"][0]
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
    assert mirage["evidence"] == "Shazam heard it 11 times from 06:00; also in the description"
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


async def test_a_set_identified_before_shows_while_another_set_is_listened_to(application, setup):
    # Live, Shazam's rate limits stretched one listen to an hour; `djlib set` on a set
    # identified earlier waited behind it the whole time.
    await run(application, submit(application))
    gate = asyncio.Event()

    async def hold(at):
        await gate.wait()

    setup.listener.hook = hold
    asked = len(setup.listener.heard)
    listening = submit(application, url="https://soundcloud.com/lumen/other-set")
    task = asyncio.create_task(IdentifyRunner(application).run())
    try:
        for _ in range(300):
            if len(setup.listener.heard) > asked:
                break
            await asyncio.sleep(0.01)
        shown = submit(application)
        assert "ahead" not in (application.job(shown)["result"].get("progress") or {})
        for _ in range(300):
            if application.job(shown)["state"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert application.job(shown)["result"]["reused"] is True
        assert application.job(listening)["state"] == "running"
        gate.set()
        for _ in range(300):
            if application.job(listening)["state"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert application.job(listening)["state"] == "completed"
    finally:
        gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_work_on_one_set_waits_for_the_job_already_on_it(application, setup):
    await run(application, submit(application))
    again = submit(application, again=True)
    shown = submit(application)
    assert application.job(shown)["result"]["progress"] == {"phase": "queued", "ahead": 1}
    runner = IdentifyRunner(application)
    assert runner.claim_reuse() is None  # the --again listen of the set goes first
    assert runner.claim()[0] == again
    assert runner.claim_reuse() is None and runner.claim() is None  # the set is busy


# -- staying honest and robust -------------------------------------------------------------------


async def test_a_set_identified_before_takes_a_tracklist_published_since(application, setup):
    await run(application, submit(application))
    setup.page["description"] = (
        "Tracklist:\n1. Lumen - Halo\n2. Nia Okoro - Slow Burn\n3. Bicep - Glue"
    )
    # `djlib set LINK` reads the page again (POST /sources/inspect) before it identifies.
    identification.remember_page({**setup.page, "url": URL}, 1000)
    reused = (await run(application, submit(application)))["result"]
    assert reused["reused"] is True and setup.fetched == [URL]
    assert [t["title"] for t in reused["tracklist"]["tracks"]] == ["Halo", "Slow Burn", "Glue"]
    assert (
        "Bicep - Glue" in json.loads((folder(application) / "page.json").read_text())["description"]
    )


async def test_a_mixesdb_lookup_that_failed_is_asked_again(application, setup, monkeypatch):
    found = {
        "source": "mixesdb",
        "page_title": "2026-10-01 - Lumen @ Friday",
        "page_url": "https://www.mixesdb.com/w/2026-10-01_-_Lumen_@_Friday",
        "text": "1. Lumen - Halo\n2. Nia Okoro - Slow Burn\n3. Bicep - Glue\n",
        "complete": True,
    }
    answers = [AppError("SOURCE_TIMEOUT", "MixesDB did not answer in time.", 504, True), found]

    async def lookup(**wanted):
        assert wanted["strict"] is True
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(identification, "mixesdb_lookup", lookup)
    first = (await run(application, submit(application)))["result"]
    assert identification.mixesdb_used(first) is None
    page = json.loads((folder(application) / "page.json").read_text())
    assert page["mixesdb"] is None and page["mixesdb_checked_at"] is None
    # Nothing of a failed lookup is kept, so the next run asks MixesDB again.
    reused = (await run(application, submit(application)))["result"]
    assert identification.mixesdb_used(reused)["page_url"] == found["page_url"]
    assert [t["title"] for t in reused["tracklist"]["tracks"]][-1] == "Glue" and not answers


async def test_recognition_failing_on_most_windows_is_no_tracklist(application, setup):
    broken = AppError("RECOGNITION_FAILED", "Shazam could not read this part of the set.", 502)
    for at in (39.0, 99.0, 159.0, 219.0):
        setup.listener.failures[at] = broken
    job_id = submit(application)
    failed = await run(application, job_id)
    error = failed["result"]["error"]
    assert failed["state"] == "failed" and error["code"] == "RECOGNITION_FAILING"
    assert error["retryable"] and error["details"]["resumes"] is True
    assert "couldn't hear 4 of 10 parts of the set" in error["message"]
    assert identification.finished(application.workspace, URL) is None
    assert (folder(application) / "audio").exists()  # kept for the retry
    # Trying again asks only the windows that failed.
    setup.listener.failures.clear()
    setup.listener.heard.clear()
    application.control(job_id, "retry")
    done = await run(application, job_id)
    assert done["state"] == "completed"
    assert [at for at in setup.listener.heard if (at - 9) % 30 == 0] == [39.0, 99.0, 159.0, 219.0]
    assert done["result"]["tracklist"]["sources"][0]["errors"] == 0


async def test_a_few_failed_windows_are_reported(application, setup):
    setup.listener.failures[99.0] = AppError("RECOGNITION_FAILED", "Shazam could not read.", 502)
    result = (await run(application, submit(application)))["result"]
    assert any("Shazam couldn't hear 1 of " in n for n in result["notices"])


async def test_retryable_download_errors_keep_the_partial_download(application, setup):
    async def failing(url, destination):
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "set.webm.part").write_bytes(b"half a set")
        raise AppError("SOURCE_TIMEOUT", "Source retrieval timed out.", 504, True)

    web.fetch_set_audio = failing
    job = await run(application, submit(application))
    assert job["result"]["error"]["code"] == "SOURCE_TIMEOUT"
    assert (folder(application) / "audio" / "set.webm.part").exists()

    async def gone(url, destination):
        raise AppError("SOURCE_UNAVAILABLE", "The public recording is unavailable here.", 502)

    web.fetch_set_audio = gone
    job = await run(application, submit(application))
    assert not (folder(application) / "audio").exists()


async def test_a_download_that_came_out_short_leaves_the_rest_unheard(application, setup):
    setup.seconds = 200.0  # the page says 300
    result = (await run(application, submit(application)))["result"]
    assert result["source"]["duration_seconds"] == 300.0
    gaps = result["tracklist"]["coverage"]["gaps"]
    assert gaps[-1]["end"] == 300.0 and gaps[-1]["start"] <= 200.0
    assert any("only 03:20 of this 05:00 set" in n for n in result["notices"])


async def test_no_room_for_the_set_fails_before_downloading(application, setup, monkeypatch):
    usage = identification.shutil.disk_usage(application.workspace.root)
    monkeypatch.setattr(
        identification.shutil,
        "disk_usage",
        lambda path: type(usage)(usage.total, usage.total - 1000, 1000),
    )
    job = await run(application, submit(application))
    error = job["result"]["error"]
    assert error["code"] == "DISK_FULL" and error["retryable"] and setup.fetched == []
    assert "Listening to this set needs about" in error["message"]


async def test_the_runner_reports_a_full_disk_and_outlives_a_catalog_it_cannot_write(
    application, setup, monkeypatch
):
    import sqlite3

    first = submit(application)
    second = submit(application, url="https://soundcloud.com/lumen/other-set")
    runner = IdentifyRunner(application)
    real_heard, real_fail = runner.heard, runner.fail
    failed_once = []

    def heard(attempt, done, total):
        if attempt.job_id == first and done == 3:
            raise sqlite3.OperationalError("database or disk is full")
        return real_heard(attempt, done, total)

    def fail(job_id, generation, error):
        if not failed_once:
            failed_once.append(error.code)
            raise sqlite3.OperationalError("database is locked")
        return real_fail(job_id, generation, error)

    monkeypatch.setattr(runner, "heard", heard)
    monkeypatch.setattr(runner, "fail", fail)
    task = asyncio.create_task(runner.run())
    try:
        for _ in range(300):
            if application.job(second)["state"] == "completed":
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert failed_once == ["DISK_FULL"]
    job = application.job(first)
    assert job["state"] == "failed" and job["result"]["error"]["code"] == "DISK_FULL"
    assert application.job(second)["state"] == "completed"


async def test_waiting_on_the_recognizer_shows_in_progress(application, setup):
    from types import SimpleNamespace

    from djlib.interfaces.identify_cli import progress_line

    setup.listener.pace = SimpleNamespace(on_wait=None)
    job_id = submit(application)
    seen = []

    async def throttled(at):
        if at == 39.0:
            setup.listener.pace.on_wait(20.0, "rate_limited", 880.0)
            seen.append(application.job(job_id))

    setup.listener.hook = throttled
    await run(application, job_id)
    progress = seen[0]["result"]["progress"]
    assert progress["waiting"] == {
        "service": "Shazam",
        "reason": "rate_limited",
        "retry_in": 20,
        "gives_up_in": 880,
    }
    # While Shazam makes djlib wait, no time left is promised.
    assert progress["eta_seconds"] is None and progress["waited_seconds"] == 0
    line = progress_line(seen[0])
    assert line == "Listening… 0:30 of 5:00 · Shazam is slow, waiting (Ctrl-C is safe)"
    assert setup.listener.pace.on_wait is None  # only while this job listens


def test_time_spent_waiting_on_the_recognizer_adds_up(application, tmp_path):
    runner = IdentifyRunner(application)
    attempt = identification._Attempt("job_1", 1, {}, tmp_path, {}, phase="listening")
    attempt.seconds, attempt.waited = 3600.0, 30.0
    attempt.wait_started = time.monotonic() - 12  # waiting for 12 seconds now
    assert runner.set_time(attempt, 40, 120) == {
        "position_seconds": 1200,
        "duration_seconds": 3600,
        "waited_seconds": 42,
    }
    assert runner.set_time(attempt, 120, 120)["position_seconds"] == 3600  # the end
    attempt.phase = "checking_gaps"
    assert runner.set_time(attempt, 3, 20)["position_seconds"] is None


async def test_a_given_tracklist_with_an_unknown_source_is_a_tracklist_file(application, setup):
    listing = {"source": "audio", "text": "Lumen - Halo\nNia Okoro - Slow Burn"}
    job = await run(application, submit(application, tracklists=[listing]))
    assert job["state"] == "completed"
    saved = json.loads((folder(application) / "listings.json").read_text())["listings"]
    assert saved[0]["source"] == "file"


async def test_listening_again_keeps_the_earlier_tracklist_until_it_finishes(application, setup):
    first = (await run(application, submit(application)))["result"]
    setup.listener.failures[9.0] = AppError(
        "RECOGNIZER_REJECTED", "Shazam refused djlib's requests (HTTP 403).", 502, True
    )
    again = await run(application, submit(application, again=True))
    error = again["result"]["error"]
    assert again["state"] == "failed" and "earlier tracklist is kept" in error["message"]
    kept = identification.finished(application.workspace, URL)
    assert kept["tracklist"] == first["tracklist"]
    # A plain run uses the earlier tracklist again, without listening.
    setup.listener.failures.clear()
    heard = len(setup.listener.heard)
    reused = (await run(application, submit(application)))["result"]
    assert reused["reused"] is True and len(setup.listener.heard) == heard
    assert reused["tracklist"] == first["tracklist"]
    assert not (folder(application) / identification.PREVIOUS).exists()
    # A finished listen replaces it.
    done = (await run(application, submit(application, again=True)))["result"]
    assert done["reused"] is False and not (folder(application) / "previous").exists()


async def test_listener_comments_that_take_too_long_are_skipped(application, setup, monkeypatch):
    import djlib.interfaces.service as service_module

    asked = []

    async def slow_comments(url, comments=0):
        asked.append(comments)
        if comments:
            raise AppError("SOURCE_TIMEOUT", "Source retrieval timed out.", 504, True)
        return {**setup.page, "url": url}

    monkeypatch.setattr(web, "inspect_source", slow_comments)
    monkeypatch.setattr(service_module, "inspect_source", slow_comments)
    result = (await run(application, submit(application)))["result"]
    assert asked == [identification.COMMENTS, 0]
    assert any("comments took too long" in notice for notice in result["notices"])


async def test_audio_left_behind_goes_after_a_day(application, setup):
    import os
    import time

    job_id = submit(application)

    async def pause(at):
        if at == 39.0:
            application.control(job_id, "pause")
            await asyncio.sleep(1)

    setup.listener.hook = pause
    await run(application, job_id)
    audio = folder(application) / "audio"
    assert identification.sweep_audio(application) == [] and audio.exists()
    old = time.time() - 2 * identification.AUDIO_SECONDS
    for path in [audio, *audio.iterdir()]:
        os.utime(path, (old, old))
    application.control(job_id, "resume")  # queued again: its audio stays
    assert identification.sweep_audio(application) == [] and audio.exists()
    application.control(job_id, "cancel")
    assert identification.sweep_audio(application) == [folder(application).name]
    assert not audio.exists() and (folder(application) / "probes.jsonl").exists()


def test_settings_the_service_did_not_read_say_to_restart_it(application, monkeypatch):
    def unconfigured(name=None):
        raise AppError(
            "RECOGNIZER_NOT_CONFIGURED", "AudD needs an API token: set DJLIB_AUDD_TOKEN."
        )

    monkeypatch.setattr(identification, "recognizer", unconfigured)
    with pytest.raises(AppError) as refused:
        submit(application, recognizer="audd")
    assert refused.value.code == "RECOGNIZER_NOT_CONFIGURED"
    assert "run djlib service stop, then try again" in refused.value.message
