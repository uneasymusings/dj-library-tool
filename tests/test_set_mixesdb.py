"""MixesDB's tracklist for a set link: looked up automatically by `djlib set LINK`, the
identify job and the MCP tools, and named so the DJ can check the page.

MixesDB answers with replies recorded from www.mixesdb.com (tests/data/mixesdb); the set's
page, download and recognizer are the fakes of tests/test_identification.py.
"""

import asyncio
import json

import pytest
from mcp import Client

from djlib.application import identification
from djlib.domain.errors import AppError
from djlib.interfaces import mcp_server
from djlib.sources import mixesdb
from tests.test_headline_workflow import build
from tests.test_identification import URL, Setup, folder, run, submit
from tests.test_identify_cli import fakes, invoke
from tests.test_mixesdb import Wiki, reply
from tests.test_rekordbox_push import FakeRekordbox

KAYTRANADA = "https://www.youtube.com/watch?v=-5EQIiabJvk"
MONTREAL = {
    "page_title": "2013-09-27 - Kaytranada @ Boiler Room Montreal 002",
    "page_url": "https://www.mixesdb.com/w/2013-09-27_-_Kaytranada_@_Boiler_Room_Montreal_002",
    "tracklist": "complete",
    "match": {"by": "url", "score": 1.0},
}
FRED = "https://www.youtube.com/watch?v=c0-hvjV2A5Y"
LONDON = "2022-06-29 - Fred Again.. @ Boiler Room, London"


@pytest.fixture
def setup(monkeypatch):
    return fakes(monkeypatch)


@pytest.fixture
def wiki(monkeypatch):
    """MixesDB, answering every request as the test says (nothing found by default)."""
    found = Wiki()

    async def lookup(**wanted):
        async with found.client() as client:
            return await mixesdb.lookup(client=client, **wanted)

    monkeypatch.setattr(identification, "mixesdb_lookup", lookup)
    return found


# YouTube names the chapters of some sets itself; none of them is a track.
YOUTUBE_CHAPTERS = [
    (0, "Opening performance"),
    (279, "Interaction and engagement"),
    (524, "Audience participation"),
    (773, "Reflective musical interlude"),
    (1129, "High-energy dance routine"),
    (1200, "Thematic musical exploration"),
    (1496, "Dynamic sonic transitions"),
    (2006, "Climactic club atmosphere"),
    (2373, "Grand finale"),
]


def kaytranada(setup: Setup, wiki: Wiki) -> None:
    """Kaytranada's Boiler Room Montreal set as YouTube showed it on 2026-10-13: no tracklist
    on the page, a complete one on MixesDB, whose page links the upload."""
    setup.page.update(
        title="Kaytranada | Boiler Room: Montreal",
        uploader="Boiler Room",
        duration_seconds=2525,
        description="As always Kaytranada comes up big live from the Boiler Room in Montreal.\n\n"
        "Get the full tracklist at: https://blrrm.tv/kaytranadatracklist\n\n"
        "► Subscribe to Boiler Room: https://blrrm.tv/YT",
        chapters=[
            {"start_time": start, "end_time": None, "title": title}
            for start, title in YOUTUBE_CHAPTERS
        ],
    )
    setup.seconds = 2525.0
    wiki.answer = lambda query: reply("kaytranada-montreal.json")


def fred_again(setup: Setup, wiki: Wiki) -> None:
    """Fred again..'s Boiler Room London: MixesDB's tracklist is incomplete."""
    setup.page.update(title="Fred again.. | Boiler Room: London", duration_seconds=4305)
    setup.seconds = 4305.0
    wiki.answer = lambda query: reply("fred-again-boiler-room.json")


def unavailable(name=None):
    raise AppError("DEPENDENCY_REQUIRED", "Song recognition is not installed.")


# -- the identify job ----------------------------------------------------------------------------


async def test_an_identification_merges_the_sets_mixesdb_tracklist(application, setup, wiki):
    kaytranada(setup, wiki)
    setup.listener.songs = [(45, 188, "Robert Glasper", "Move Love (Kaytranada Edition)")]
    result = (await run(application, submit(application, url="https://youtu.be/-5EQIiabJvk")))[
        "result"
    ]
    [entry] = [s for s in result["tracklist"]["sources"] if s["source"] == "mixesdb"]
    assert entry == {
        "source": "mixesdb",
        "entries": 22,
        "named": 22,
        "unknown": 0,
        "heard": 1,
        **MONTREAL,
    }
    found = identification.summary(result)
    assert found["mixesdb"] == MONTREAL and found["listed"] is True
    tracks = {track["n"]: track for track in found["tracks"]}
    # "Edition" names a version, like "Edit" or "Mix".
    assert (tracks[2]["title"], tracks[2]["version"], tracks[2]["status"]) == (
        "Move Love",
        "Kaytranada Edition",
        "confirmed",
    )
    assert tracks[2]["evidence"] == "Shazam: 4 windows from 00:54; also in MixesDB"
    assert (tracks[5]["artist"], tracks[5]["evidence"]) == ("SebastiAn", "From MixesDB")
    assert len(tracks) == 22
    page = json.loads((folder(application, KAYTRANADA) / "page.json").read_text())
    assert page["mixesdb"]["page_url"] == MONTREAL["page_url"]

    # Reusing the identification reads page.json, not MixesDB.
    asked = list(wiki.asked)
    identification.MIXESDB_LOOKUPS.clear()
    reused = (await run(application, submit(application, url=KAYTRANADA)))["result"]
    assert reused["reused"] is True and wiki.asked == asked
    assert identification.mixesdb_used(reused) == MONTREAL


async def test_a_mixesdb_tracklist_the_dj_gave_stands_in_for_the_lookup(application, setup, wiki):
    kaytranada(setup, wiki)
    given = {"source": "mixesdb", "text": "Robert Glasper - Move Love (Kaytranada Edition)\n"}
    result = (await run(application, submit(application, url=KAYTRANADA, tracklists=[given])))[
        "result"
    ]
    [entry] = [s for s in result["tracklist"]["sources"] if s["source"] == "mixesdb"]
    assert entry["entries"] == 1 and "page_url" not in entry
    assert identification.mixesdb_used(result) is None


async def test_a_broken_lookup_never_stops_an_identification(application, setup, monkeypatch):
    async def broken(**wanted):
        raise ValueError("wiki text djlib didn't expect")

    monkeypatch.setattr(identification, "mixesdb_lookup", broken)
    job = await run(application, submit(application))
    assert job["state"] == "completed" and identification.mixesdb_used(job["result"]) is None
    assert json.loads((folder(application) / "page.json").read_text())["mixesdb"] is None


# -- the coordinator -----------------------------------------------------------------------------


def test_inspect_asks_mixesdb_only_when_the_page_lists_no_tracklist(library_http, setup, wiki):
    kaytranada(setup, wiki)

    def inspect(url, **asked):
        body = {"url": url, "comments": identification.COMMENTS, **asked}
        return library_http.post("/sources/inspect", json=body).json()["result"]

    assert "mixesdb" not in inspect(KAYTRANADA) and wiki.asked == []
    found = inspect(KAYTRANADA, mixesdb=True)["mixesdb"]
    assert {key: found[key] for key in MONTREAL} == MONTREAL
    assert (
        found["text"].splitlines()[1]
        == "2. [00:45] Robert Glasper - Move Love (Kaytranada Edition)"
    )
    asked = list(wiki.asked)
    # A tracklist in the description counts, though YouTube's chapter names come first.
    setup.page["description"] = "1. Lumen - Halo\n2. Bicep - Glue\n3. Nia Okoro - Slow Burn\n"
    assert inspect(URL, mixesdb=True)["mixesdb"] is None and wiki.asked == asked


# -- djlib set LINK ------------------------------------------------------------------------------


def test_set_uses_a_complete_mixesdb_tracklist_without_listening(
    application, library_http, setup, wiki, audio_factory, monkeypatch
):
    kaytranada(setup, wiki)
    build(library_http, [audio_factory("arabest.wav", frequency=330)], [("SebastiAn", "Arabest")])
    FakeRekordbox(monkeypatch)
    envelope = json.loads(invoke(application, "set", KAYTRANADA).stdout)
    result = envelope["result"]
    # Complete and without IDs: like a published tracklist, nothing to listen for.
    assert result["identified"] is None and setup.fetched == []
    assert (result["name"], result["songs"], result["owned"]) == (
        "Kaytranada | Boiler Room: Montreal",
        22,
        1,
    )
    assert result["mixesdb"] == MONTREAL and envelope["warnings"] == []

    shown = invoke(application, "set", KAYTRANADA, "--no-identify", pretty=True)
    assert shown.exit_code == 0, shown.output
    assert f"Tracklist from MixesDB: {MONTREAL['page_title']}" in shown.stdout
    assert MONTREAL["page_url"] in shown.stdout


def test_set_listens_to_a_set_whose_mixesdb_tracklist_is_incomplete(
    application, library_http, setup, wiki, monkeypatch
):
    fred_again(setup, wiki)
    FakeRekordbox(monkeypatch)
    result = json.loads(invoke(application, "set", FRED).stdout)["result"]
    assert result["identified"]["counts"]["tracks"] >= 25 and setup.fetched == [FRED]
    assert result["mixesdb"]["page_title"] == LONDON
    assert result["mixesdb"]["tracklist"] == "incomplete"
    # The identification reused what `djlib set` looked up a moment before.
    assert wiki.asked == ['"c0-hvjV2A5Y"']


def test_without_song_recognition_set_uses_the_mixesdb_tracklist_and_says_so(
    application, library_http, wiki, monkeypatch
):
    found = Setup(monkeypatch)
    fred_again(found, wiki)
    monkeypatch.setattr(identification, "recognizer", unavailable)
    FakeRekordbox(monkeypatch)
    envelope = json.loads(invoke(application, "set", FRED).stdout)
    result = envelope["result"]
    assert result["identified"] is None and result["songs"] == 25
    assert result["mixesdb"]["page_title"] == LONDON
    assert envelope["warnings"] == [
        "djlib didn't listen to the set: song recognition isn't installed; install it with: "
        f"{identification.INSTALLER}. This uses the tracklist on MixesDB.",
        "MixesDB marks this tracklist incomplete: songs may be missing.",
    ]


def test_no_tracklist_anywhere_says_mixesdb_had_none(
    application, library_http, setup, wiki, monkeypatch
):
    FakeRekordbox(monkeypatch)
    error = json.loads(invoke(application, "set", URL, "--no-identify").stdout)["error"]
    assert error["code"] == "TRACKLIST_NOT_FOUND"
    assert error["message"].startswith(
        "This upload has no tracklist in its description or chapters, and djlib found none on "
        "MixesDB."
    )
    assert wiki.asked  # MixesDB was asked, and had no page for the set


# -- djlib identify and MCP ----------------------------------------------------------------------


def test_identify_names_the_mixesdb_page(application, library_http, setup, wiki):
    kaytranada(setup, wiki)
    shown = invoke(application, "identify", KAYTRANADA, pretty=True)
    assert shown.exit_code == 0, shown.output
    assert f"Tracklist from MixesDB: {MONTREAL['page_title']}" in shown.stdout
    envelope = json.loads(invoke(application, "identify", KAYTRANADA).stdout)
    assert envelope["result"]["mixesdb"] == MONTREAL
    # YouTube's own chapter names are no tracklist, so none is reported as a skipped line.
    assert envelope["warnings"] == [] and envelope["result"]["listed"] is True


async def test_mcp_tools_name_the_mixesdb_page(application, library_http, setup, wiki):
    kaytranada(setup, wiki)
    async with Client(mcp_server.build_server(application.workspace)) as client:
        reply_ = await client.call_tool(
            "djlib_source_inspect", {"url": KAYTRANADA, "comments": identification.COMMENTS}
        )
        assert reply_.structured_content["result"]["mixesdb"]["page_url"] == MONTREAL["page_url"]
        reply_ = await client.call_tool(
            "djlib_identify_set", {"url": KAYTRANADA, "idempotency_key": "mixesdb-1"}
        )
        job = reply_.structured_content["result"]
        for _ in range(200):
            reply_ = await client.call_tool("djlib_job", {"job_id": job["job_id"]})
            job = reply_.structured_content["result"]
            if job["state"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
    assert job["state"] == "completed"
    assert job["result"]["mixesdb"] == MONTREAL
