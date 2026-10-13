"""`djlib identify`, `djlib set` with identification, the MCP tools and doctor's row.

Everything runs through the real coordinator (its identification runner included); the page,
the download and the recognizer are the fakes of tests/test_identification.py.
"""

import json

import pytest
from typer.testing import CliRunner

from djlib.application import identification
from djlib.interfaces import identify_cli
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from tests.test_identification import URL, Setup

THREE_SONGS = [
    (0, 100, "Lumen", "Halo"),
    (100, 200, "Bicep", "Glue"),
    (200, 300, "Nia Okoro", "Slow Burn"),
]
LISTED = "Lumen - Halo\nID - ID\nNia Okoro - Slow Burn\n"


def fakes(monkeypatch) -> Setup:
    """The fake page, download and recognizer, with three songs in a five-minute set."""
    monkeypatch.setattr(identify_cli, "POLL_SECONDS", 0.02)
    found = Setup(monkeypatch)
    found.listener.songs = THREE_SONGS
    return found


@pytest.fixture
def setup(monkeypatch):
    return fakes(monkeypatch)


def invoke(application, *arguments, pretty=False, columns=160):
    env = {
        "DJLIB_OUTPUT": "pretty" if pretty else None,
        "COLUMNS": str(columns),
        "NO_COLOR": "1",
        "FORCE_COLOR": None,
        "GITHUB_ACTIONS": None,
        "TTY_COMPATIBLE": None,
        "TTY_INTERACTIVE": None,
    }
    workspace = ["--workspace", str(application.workspace.root)]
    return CliRunner(env=env).invoke(cli_app, [*workspace, *map(str, arguments)])


def events(reply) -> list[dict]:
    return [json.loads(line) for line in reply.stderr.splitlines() if line.startswith("{")]


# -- djlib identify ------------------------------------------------------------------------------


def test_identify_lists_every_song_with_how_sure_djlib_is(
    application, library_http, setup, tmp_path
):
    listing = tmp_path / "1001-friday.txt"
    listing.write_text(LISTED, encoding="utf-8")
    reply = invoke(application, "identify", "https://youtu.be/fridayset1", "--tracklist", listing)
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    result = envelope["result"]
    assert result["source"]["url"] == URL and result["duration"] == "05:00"
    assert result["recognizer"] == {"name": "shazam", "licensed": False}
    assert result["counts"] == {
        "tracks": 3,
        "confirmed": 2,
        "likely": 1,
        "weak": 0,
        "conflict": 0,
        "unidentified": 0,
    }
    halo, glue_id, burn = result["tracks"]
    assert halo == {
        "n": 1,
        "start": "00:09",
        "approx": False,
        "kind": "named",
        "artist": "Lumen",
        "title": "Halo",
        "version": "",
        "status": "confirmed",
        "evidence": "Shazam: 4 windows from 00:09; also in 1001Tracklists",
    }
    # The ID stays an ID; what Shazam heard there is a candidate, never its name.
    assert (glue_id["kind"], glue_id["artist"], glue_id["title"]) == ("unknown", None, None)
    assert glue_id["status"] == "likely"
    assert glue_id["candidates"][0]["label"] == "Bicep - Glue"
    assert glue_id["evidence"].startswith("ID (likely Bicep - Glue): Shazam heard it in 3 windows")
    assert burn["title"] == "Slow Burn"
    assert [source["source"] for source in result["sources"]] == ["audio", "1001tracklists"]
    assert result["next"] == [f"djlib set {URL}"]
    assert result["tracklist_file"].endswith("tracklist.txt")
    # The one-time note about the unofficial Shazam service.
    assert envelope["warnings"] == [identification.UNOFFICIAL_SHAZAM]
    # Assistants running it in the background see the job and its phases on stderr.
    seen = events(reply)
    assert seen[0] == {
        "event": "identify_started",
        "job_id": result["job_id"],
        "url": "https://youtu.be/fridayset1",
    }
    progress = [event for event in seen if event["event"] == "identify_progress"]
    assert progress and progress[-1]["phase"] == "completed"
    assert {event["phase"] for event in progress} <= {
        "queued",
        "reading_page",
        "downloading",
        "listening",
        "checking_gaps",
        "merging",
        "completed",
    }


def test_identify_without_a_tracklist_suggests_one_when_little_was_named(
    application, library_http, setup
):
    setup.listener.songs = [(0, 100, "Lumen", "Halo")]
    reply = invoke(application, "identify", URL)
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert [track["status"] for track in result["tracks"]] == ["confirmed", "unidentified"]
    assert result["coverage"]["percent"] < identify_cli.LOW_COVERAGE
    assert result["next"] == [f"djlib set {URL}", f"djlib identify {URL} --tracklist FILE"]


def test_identify_reads_as_a_numbered_table_in_a_terminal(application, library_http, setup):
    setup.page["description"] = LISTED
    reply = invoke(application, "identify", URL, pretty=True)
    assert reply.exit_code == 0, reply.output
    text = reply.stdout
    assert "Friday Set" in text and "YouTube · 05:00" in text
    assert "3 tracks, 67% of the set named" in text and "2 confirmed, 1 likely" in text
    rows = [line for line in text.splitlines() if "Halo" in line or "Glue" in line]
    assert "✓ confirmed" in rows[0] and "Lumen - Halo" in rows[0]
    assert "ID — likely Bicep - Glue" in rows[1]
    assert "Shazam  unofficial; may slow down or stop working" in text
    assert f"set '{URL}'" in text  # the next step, quoted for the shell


def test_ctrl_c_cancels_the_identification(application, library_http, setup, monkeypatch):
    real = LocalClient.request
    asked = []

    def interrupted(self, method, path, **kwargs):
        if method == "GET" and path.startswith("/jobs/") and not asked:
            asked.append(path)
            raise KeyboardInterrupt
        return real(self, method, path, **kwargs)

    monkeypatch.setattr(LocalClient, "request", interrupted)
    reply = invoke(application, "identify", URL)
    assert reply.exit_code == 130
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "JOB_CANCELLED" and "run the same command" in error["message"]
    job = library_http.get(f"/jobs/{error['details']['job_id']}").json()["result"]
    assert job["state"] == "cancelled"


def test_identify_failures_explain_what_to_do(application, library_http, setup):
    setup.page["duration_seconds"], setup.seconds = 3600, 30.0
    reply = invoke(application, "identify", URL)
    assert reply.exit_code == 2
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "SET_PREVIEW_ONLY" and error["details"]["job_id"]
    pretty = invoke(application, "identify", URL, pretty=True)
    assert "Only a preview of that set plays" in pretty.stderr


def test_identify_needs_song_recognition(application, library_http):
    reply = invoke(application, "identify", URL)
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "DEPENDENCY_REQUIRED" and "--python 3.12" in error["message"]
    browser = json.loads(
        invoke(application, "identify", "https://www.1001tracklists.com/tracklist/1/x").stdout
    )["error"]
    assert browser["code"] == "SOURCE_BROWSER_ONLY"


def test_identify_listens_with_the_chosen_recognizer(application, library_http, setup):
    setup.listener.name, setup.listener.licensed = "audd", True
    reply = invoke(application, "identify", URL, "--recognizer", "audd")
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    assert envelope["result"]["recognizer"] == {"name": "audd", "licensed": True}
    assert set(setup.made) == {"audd"} and envelope["warnings"] == []
    wrong = invoke(application, "identify", URL, "--recognizer", "acr")
    assert wrong.exit_code == 2 and "acrcloud" in wrong.output


def test_progress_lines_say_what_djlib_is_doing():
    def job(phase, state="running", **progress):
        return {"state": state, "result": {"progress": {"phase": phase, **progress}}}

    listening = job("listening", windows_done=34, windows_total=124, songs_heard=6, eta_seconds=130)
    assert identify_cli.progress_line(listening) == (
        "Listening to the set… 34 of 124 windows · 6 songs so far · about 2 min left"
    )
    assert identify_cli.progress_line(job("checking_gaps", windows_total=30, eta_seconds=40)) == (
        "Listening again where songs change… 0 of 30 windows · less than a minute left"
    )
    downloading = job("downloading", downloaded_bytes=45_200_000)
    assert identify_cli.progress_line(downloading) == "Downloading the set's audio… 45 MB"
    assert identify_cli.progress_line({"state": "queued", "result": {}}) == (
        "Waiting for the set before it to finish…"
    )


def test_watching_an_identify_job_shows_its_windows():
    from djlib.interfaces import terminal

    running = {
        "job_id": "job_1",
        "kind": "identify",
        "state": "running",
        "result": {"progress": {"phase": "listening", "windows_done": 3, "windows_total": 10}},
    }
    done = {**running, "state": "completed", "result": {}}

    class Jobs:
        def request(self, method, path, **kwargs):
            return {"result": done}

    followed = terminal.follow(terminal.Terminal(json=False), Jobs(), running)
    assert followed.job["state"] == "completed" and not followed.detached


def test_lines_a_tracklist_could_not_use_are_warnings(application, library_http, setup, tmp_path):
    listing = tmp_path / "friday.txt"
    listing.write_text("Friday at Fabric\n" + LISTED + "just a note\n", encoding="utf-8")
    envelope = json.loads(invoke(application, "identify", URL, "--tracklist", listing).stdout)
    # The heading names the set; the note is reported.
    assert envelope["warnings"][1:] == [
        "The tracklist file, line 5 skipped (no “Artist - Title” separator): just a note"
    ]
