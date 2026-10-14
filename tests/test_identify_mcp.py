"""The MCP tools for set links: identify a set (a job to poll) and list Shazam history."""

import asyncio
import json

import pytest
from mcp import Client

from djlib.application import identification
from djlib.interfaces import mcp_server
from tests.test_identify_cli import LISTED, fakes


@pytest.fixture
def setup(monkeypatch):
    return fakes(monkeypatch)


async def test_mcp_identifies_a_set_and_trims_the_job(application, library_http, setup):
    async with Client(mcp_server.build_server(application.workspace)) as client:
        reply = await client.call_tool(
            "djlib_identify_set",
            {
                "url": "https://youtu.be/fridayset1",
                "idempotency_key": "mcp-1",
                "tracklist_text": LISTED,
                "tracklist_source": "1001Tracklists",
            },
        )
        job = reply.structured_content["result"]
        assert job["kind"] == "identify"
        for _ in range(200):
            reply = await client.call_tool("djlib_job", {"job_id": job["job_id"]})
            job = reply.structured_content["result"]
            if job["state"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
    assert job["state"] == "completed"
    result = job["result"]
    assert "tracklist" not in result  # the raw evidence stays in tracklist_json
    assert [track["status"] for track in result["tracks"]] == ["confirmed", "likely", "confirmed"]
    assert result["tracks"][1]["candidates"][0]["label"] == "Bicep - Glue"
    # Each timed track links to that moment, for an assistant to write as a markdown link.
    assert result["tracks"][0]["listen_url"] == "https://www.youtube.com/watch?v=fridayset1&t=9s"
    assert result["counts"]["tracks"] == 3 and result["coverage"]["percent"] == 67
    assert result["notices"] == [identification.UNOFFICIAL_SHAZAM]
    assert len(json.dumps(result)) < 8000


async def test_identify_set_replies_like_djlib_job_even_on_a_replay(
    application, library_http, setup
):
    arguments = {"url": "https://youtu.be/fridayset1", "idempotency_key": "mcp-replay"}
    async with Client(mcp_server.build_server(application.workspace)) as client:
        job = (await client.call_tool("djlib_identify_set", arguments)).structured_content
        job_id = job["result"]["job_id"]
        for _ in range(200):
            read = await client.call_tool("djlib_job", {"job_id": job_id})
            if read.structured_content["result"]["state"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
        # The same key again: the finished job, in the shape djlib_job gives.
        replay = (await client.call_tool("djlib_identify_set", arguments)).structured_content
    result = replay["result"]
    assert result["job_id"] == job_id and result["state"] == "completed"
    assert "tracklist" not in result["result"]
    assert result["result"]["coverage"]["percent"] == 97  # no ID: nearly all of it named
    assert (
        result["result"]["next"][0]
        == "djlib set 'https://www.youtube.com/watch?v=fridayset1' --no-rekordbox"
    )
    assert result["result"] == read.structured_content["result"]["result"]


async def test_a_copied_tracklist_is_not_asked_for_again(application, library_http, setup):
    """tracklist_text without tracklist_source (a plain file): the ID it leaves is no reason
    to ask for a tracklist again."""
    arguments = {
        "url": "https://youtu.be/fridayset1",
        "idempotency_key": "mcp-copied",
        "tracklist_text": LISTED,
    }
    async with Client(mcp_server.build_server(application.workspace)) as client:
        job = (await client.call_tool("djlib_identify_set", arguments)).structured_content
        for _ in range(200):
            job = (
                await client.call_tool("djlib_job", {"job_id": job["result"]["job_id"]})
            ).structured_content
            if job["result"]["state"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
    result = job["result"]["result"]
    assert [source["source"] for source in result["sources"]] == ["audio", "file"]
    assert any(track["kind"] == "unknown" for track in result["tracks"])
    assert result["next"] == [
        "djlib set 'https://www.youtube.com/watch?v=fridayset1' --no-rekordbox"
    ]


def test_assistants_get_tool_routes_and_the_spellings_a_song_is_owned_under():
    """Without a shell an assistant checks the set with djlib_create_request: each confirmed
    track carries the spellings it is owned under (also), as `djlib set` sends them, and
    `next` names tools instead of the CLI's clipboard command."""
    from djlib.application.set_identification import build_tracklist
    from djlib.domain.contracts import ResponseEnvelope
    from djlib.domain.request_contracts import RequestItem
    from djlib.interfaces.envelope import envelope
    from tests.test_set_identification import KAYTRANADA_HEARD, KAYTRANADA_MIXESDB, listing

    mixesdb = listing(
        "mixesdb",
        *[
            f"{n}. [{e['timestamp']}] {e['artist']} - {e['title']}"
            for n, e in enumerate(KAYTRANADA_MIXESDB["entries"], 1)
        ],
        "13. [38:00] ID - ID",
    )
    tracklist = build_tracklist(duration=2525, probes=KAYTRANADA_HEARD, listed=[mixesdb])
    for source in tracklist["sources"]:
        if source["source"] == "mixesdb":
            source["page_url"] = "https://www.mixesdb.com/w/Kaytranada"
    url = "https://soundcloud.com/platform/kaytranada-boiler-room"
    job = {
        "job_id": "job_1",
        "kind": "identify",
        "state": "completed",
        "result": {"tracklist": tracklist, "source": {"url": url}, "counts": {}},
    }
    reply = mcp_server.identify_view(ResponseEnvelope.model_validate(envelope(job)))
    result = reply.result["result"]
    glasper = next(track for track in result["tracks"] if track["title"] == "Move Love")
    assert glasper["also"] == [
        {
            "artist": "Robert Glasper",
            "title": "Move Love",
            "version": "Kaytranada Edition",
            "source": "mixesdb",
        }
    ]
    # Sent as an item, it is owned under either spelling.
    item = RequestItem.model_validate(
        {key: glasper[key] for key in ("artist", "title", "version", "also")}
    )
    assert item.also[0].artist == "Robert Glasper"
    assert not any("paste" in step for step in result["next"])
    assert result["next"][-1] == mcp_server.TRACKLIST_ROUTE
    assert result["next_without_shell"] == mcp_server.NO_SHELL_ROUTE


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.1001tracklists.com/tracklist/2x9/x.html", "1001tracklists"),
        ("https://1001.tl/14rk2xyz", "1001tracklists"),
        ("https://www.mixcloud.com/NTSRadio/some-show/", "mixcloud"),
    ],
)
async def test_assistants_are_given_tools_not_shell_commands_for_these_sites(
    application, library_http, url, site
):
    """No `djlib set paste`: an assistant has no shell here, and paste reads the clipboard."""
    async with Client(mcp_server.build_server(application.workspace)) as client:
        identify = await client.call_tool(
            "djlib_identify_set", {"url": url, "idempotency_key": f"mcp-{site}"}
        )
        inspect = await client.call_tool("djlib_source_inspect", {"url": url})
    for reply, code in ((identify, "SOURCE_BROWSER_ONLY"), (inspect, "SOURCE_UNSUPPORTED")):
        error = reply.structured_content["error"]
        assert error["code"] == (code if site == "1001tracklists" else "SOURCE_UNSUPPORTED")
        assert error["details"] == {"next": mcp_server.ASSISTANT_ROUTES[site]}
        assert "paste" not in json.dumps(error) and "djlib set" not in json.dumps(error)
    assert "tracklist_text" in mcp_server.ASSISTANT_ROUTES["1001tracklists"]


def test_another_error_for_such_a_link_keeps_its_own_way_forward():
    from djlib.domain.contracts import ResponseEnvelope
    from djlib.domain.errors import AppError
    from djlib.interfaces.envelope import envelope

    missing = AppError("WORKSPACE_REQUIRED", "No djlib workspace yet.", 409, details={"next": "x"})
    reply = ResponseEnvelope.model_validate(envelope(error=missing.as_dict()))
    assert mcp_server.assistant_route(reply, "https://1001.tl/14rk2xyz") == reply


@pytest.mark.parametrize(
    ("kind", "state", "progress", "seconds"),
    [
        ("identify", "running", {"phase": "listening"}, 15),
        ("identify", "running", {"phase": "checking_gaps"}, 15),
        ("identify", "running", {"phase": "listening", "waiting": {"service": "Shazam"}}, 30),
        ("identify", "queued", {"phase": "queued", "ahead": 1}, 15),
        ("identify", "running", {"phase": "merging"}, 2),
        ("identify", "queued", None, 2),
        ("download", "running", None, 2),
        ("identify", "completed", {"phase": "listening"}, None),
    ],
)
def test_a_listen_is_read_every_15_seconds_not_every_2(kind, state, progress, seconds):
    from types import SimpleNamespace

    from djlib.application.service import poll_after

    job = SimpleNamespace(kind=kind, state=state, result={"progress": progress} if progress else {})
    assert poll_after(job) == seconds


async def test_mcp_lists_shazam_history(application, tmp_path):
    export = tmp_path / "shazam.csv"
    export.write_text(
        "TagTime,Title,Artist\n2026-10-01T20:00:00Z,Halo,Lumen\n2026-10-02T21:00:00Z,Glue,Bicep\n",
        encoding="utf-8",
    )
    async with Client(mcp_server.build_server(application.workspace)) as client:
        reply = await client.call_tool("djlib_shazam_history", {"source": str(export)})
        result = reply.structured_content["result"]
        assert [record["title"] for record in result["records"]] == ["Glue", "Halo"]
        assert result["spec"] == f"shazam:{export}"
        limited = await client.call_tool(
            "djlib_shazam_history", {"source": str(export), "limit": 1}
        )
        assert len(limited.structured_content["result"]["records"]) == 1
        wrong = await client.call_tool("djlib_shazam_history", {"source": "new"})
        assert wrong.structured_content["error"]["code"] == "INPUT_INVALID"
        none = await client.call_tool("djlib_shazam_history", {})
        assert none.structured_content["error"]["code"] == "SHAZAM_HISTORY_NOT_FOUND"


def test_capabilities_say_sets_are_identified(application):
    capabilities = application.capabilities()
    assert {"set_identification", "shazam_history"} <= set(capabilities["implemented"])
    assert "set_recognition" not in capabilities["planned"]
    assert "djlib_identify_set" in capabilities["details"]["set_identification"]
