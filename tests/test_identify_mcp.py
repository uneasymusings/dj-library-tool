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
    assert result["result"]["next"][0] == "djlib set 'https://www.youtube.com/watch?v=fridayset1'"
    assert result["result"] == read.structured_content["result"]["result"]


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
