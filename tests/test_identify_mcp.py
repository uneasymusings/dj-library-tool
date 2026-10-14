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
