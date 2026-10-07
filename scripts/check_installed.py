"""Smoke-check an installed wheel, including its migration, skill, daemon, and MCP stdio.

Run with a clean environment's Python, outside editable installs. All state and
original audio are temporary; the coordinator is stopped before cleanup.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from filelock import FileLock, Timeout
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from djlib.application.agent_setup import create_agent_session
from djlib.application.demo import run_demo
from djlib.audio.inspection import checksum
from djlib.interfaces.client import LocalClient
from djlib.interfaces.tool_manifest import CORE_PROFILE, TOOL_NAMES
from djlib.workspace import Workspace


async def wait_for_mcp_job(call, job_id: str, *, timeout: float = 30) -> dict:
    """Bounded original-tone check; a terminal partial result is never success."""
    deadline = time.monotonic() + timeout
    while True:
        job = await call("djlib_job", {"job_id": job_id})
        if job["state"] not in {"queued", "running"}:
            assert job["state"] == "completed" and job["outcome"] == "complete", job
            return job
        assert time.monotonic() < deadline, {"timed_out": True, "job": job}
        await asyncio.sleep(0.1)


async def check_library_workflows(client, collection_id: str) -> None:
    """Exercise the installed request/organization adapters using only original demo bytes."""

    async def call(name, arguments):
        reply = await client.call_tool(name, arguments)
        assert not reply.is_error, reply
        envelope = reply.structured_content
        assert envelope["ok"], envelope
        assert envelope["error"] is None
        return envelope["result"]

    original = await call("djlib_collection", {"collection_id": collection_id})
    tracks = original["tracks"]
    hashes = {track["path"]: checksum(Path(track["path"])) for track in tracks}
    first = tracks[0]
    reference = {key: first[key] for key in ("recording_id", "asset_revision_id")}
    body = {
        "name": "Installed request trial",
        "idempotency_key": "installed-request-trial",
        "items": [
            {key: first[key] for key in ("artist", "title", "version")},
            {"kind": "unknown", "label": "Unidentified synthetic set ID", "timestamp": "00:30"},
        ],
    }
    requested = await call("djlib_create_request", {"request_body": body})
    assert requested["counts"]["satisfied"] == requested["counts"]["unknown"] == 1
    assert requested["items"][0]["accepted"]["asset_revision_id"] == first["asset_revision_id"]
    replay = await call("djlib_create_request", {"request_body": body})
    assert replay.pop("reused") is True and replay == requested
    request_id = requested["request_id"]
    page = await call("djlib_request", {"request_id": request_id, "after": 1, "limit": 1})
    assert page["items"][0]["input"]["timestamp"] == "00:30"
    refreshed = await call(
        "djlib_refresh_request",
        {"request_id": request_id, "revision": 1, "item_ids": [requested["items"][1]["item_id"]]},
    )
    assert refreshed["items"][0] == requested["items"][0]
    satisfied = await call(
        "djlib_resolve_request",
        {
            "request_id": request_id,
            "item_id": requested["items"][0]["item_id"],
            "resolution": {
                # A re-check that changes nothing keeps the revision; use what it returned.
                "revision": refreshed["revision"],
                "action": "satisfy",
                **reference,
                "notes": "Explicitly reuse the generated tone's existing catalog revision.",
            },
        },
    )
    assert satisfied["items"][1]["state"] == "unknown"
    report = await call(
        "djlib_request_report", {"request_id": request_id, "revision": satisfied["revision"]}
    )
    missing = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert missing["unresolved_items"] == 1
    assert missing["items"][0]["input"] == requested["items"][1]["input"]
    assert missing["automatic_acquisition"] is False
    metadata = await call("djlib_track_metadata", reference)
    assert metadata["acoustic_analysis_performed"] is False
    assert metadata["effective"]["bpm"]["known"] is False
    assert (await call("djlib_annotations", reference))["revision"] == 0
    annotation = await call(
        "djlib_annotate",
        {
            "request_body": {
                **reference,
                "revision": 0,
                "idempotency_key": "installed-annotation",
                "tags": ["synthetic-smoke-test"],
                "set_role": "test fixture",
                "energy": 2,
                "notes": "Synthetic label only; no native or acoustic analysis observed.",
            }
        },
    )
    assert annotation["state"] == "completed"
    patched = await call(
        "djlib_annotate",
        {
            "request_body": {
                **reference,
                "revision": 1,
                "idempotency_key": "installed-annotation-patch",
                "notes": "Updated test note; omitted fields must remain intact.",
            }
        },
    )
    stored = await call("djlib_annotations", reference)
    assert stored["revision"] == 2
    assert stored["annotations"] == patched["result"]["annotations"]
    for field in ("tags", "set_role", "energy"):
        assert stored["annotations"][field] == annotation["result"]["annotations"][field]
    organization_body = {
        "name": "Installed filtered synthetic collection",
        "idempotency_key": "installed-organization",
        "tracks": [{key: track[key] for key in reference} for track in reversed(tracks)],
        "filters": {"tags": ["synthetic-smoke-test"]},
        "unknown": "exclude",
        "order_by": "input",
    }
    organized = await call("djlib_organize", {"request_body": organization_body})
    repeated = await call("djlib_organize", {"request_body": organization_body})
    assert repeated["job_id"] == organized["job_id"]
    organized = await wait_for_mcp_job(call, organized["job_id"])
    assert organized["result"]["selected_count"] == 1
    assert organized["result"]["app_state"] == "not_tracked_here"
    assert organized["result"]["device_state"] == "not_tracked_here"
    filtered = await call(
        "djlib_collection", {"collection_id": organized["result"]["collection_id"]}
    )
    assert [track["recording_id"] for track in filtered["tracks"]] == [first["recording_id"]]
    # Annotations change only the live `dj` readout, never membership or file evidence.
    current = await call("djlib_collection", {"collection_id": collection_id})
    assert without_dj(current) == without_dj(original)
    assert current["tracks"][0]["dj"]["energy"] == annotation["result"]["annotations"]["energy"]
    assert {path: checksum(Path(path)) for path in hashes} == hashes


def without_dj(collection: dict) -> dict:
    return {
        **collection,
        "tracks": [{k: v for k, v in t.items() if k != "dj"} for t in collection["tracks"]],
    }


async def check() -> None:
    with tempfile.TemporaryDirectory(prefix="djlib-installed-") as directory:
        root = Path(directory)
        workspace = Workspace(root / "library")
        local = LocalClient(workspace)
        try:
            result = run_demo(workspace)
            assert result["ingestion"]["counts"] == {"succeeded": 3}
            assert result["export"]["outcome"] == "complete"
            session = create_agent_session(workspace, root / "assistant")
            assert session["personal_config_changed"] is False
            assert (root / "assistant/.agents/skills/dj-library/references/cli.md").is_file()
            serve = [
                "-m",
                "djlib.interfaces.cli",
                "--workspace",
                str(workspace.root),
                "mcp",
                "serve",
            ]
            # The default profile is the core set; the rest of this check needs every tool.
            default = StdioServerParameters(command=sys.executable, args=serve, cwd=root)
            async with Client(default) as client:
                core = {tool.name for tool in (await client.list_tools()).tools}
                assert core == CORE_PROFILE, sorted(core ^ CORE_PROFILE)
            transport = StdioServerParameters(
                command=sys.executable, args=serve, cwd=root, env={"DJLIB_MCP_TOOLS": "full"}
            )
            async with Client(transport) as client:
                tools = (await client.list_tools()).tools
                assert {tool.name for tool in tools} == TOOL_NAMES
                assert all(tool.output_schema for tool in tools)
                reply = await client.call_tool("djlib_capabilities", {})
                assert reply.structured_content["ok"]
                reply = await client.call_tool("djlib_delivery_targets", {})
                assert "cdj-2000nxs" in reply.structured_content["result"]["targets"]
                await check_library_workflows(
                    client, result["ingestion"]["result"]["collection_id"]
                )
                if shutil.which("ffmpeg") and shutil.which("ffprobe"):
                    planned = await client.call_tool(
                        "djlib_plan_delivery",
                        {
                            "request_body": {
                                "name": "Installed delivery pilot",
                                "workflow": "rekordbox_import",
                                "app_version": "synthetic-smoke-test",
                                "collection_ids": [result["ingestion"]["result"]["collection_id"]],
                            }
                        },
                    )
                    assert planned.structured_content["ok"]
                    delivery = planned.structured_content["result"]
                    prepared = await client.call_tool(
                        "djlib_prepare_delivery",
                        {
                            "delivery_id": delivery["delivery_id"],
                            "revision": delivery["revision"],
                            "idempotency_key": "installed-delivery-pilot",
                        },
                    )
                    job = await asyncio.to_thread(
                        local.wait, prepared.structured_content["result"]["job_id"], 20
                    )
                    assert job["result"]["outcome"] == "complete", job
                    status = await client.call_tool(
                        "djlib_delivery", {"delivery_id": delivery["delivery_id"]}
                    )
                    assert status.structured_content["result"]["prepared_for_import"]
                    assert not status.structured_content["result"]["ready_for_departure"]
                    # An engine-shaped XML file cannot stand in for native export evidence.
                    generated_xml = workspace.exports / "engine-shaped.xml"
                    generated_xml.write_text(
                        '<DJ_PLAYLISTS><PRODUCT Name="dj-library-tool" Version="test"/>'
                        '<COLLECTION Entries="0"/><PLAYLISTS/></DJ_PLAYLISTS>',
                        encoding="utf-8",
                    )
                    readback = await client.call_tool(
                        "djlib_inspect_delivery_native_xml",
                        {
                            "delivery_id": delivery["delivery_id"],
                            "revision": status.structured_content["result"]["revision"],
                            "path": str(generated_xml),
                        },
                    )
                    assert readback.structured_content["error"]["code"] == (
                        "NATIVE_XML_PRODUCT_INVALID"
                    )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "tracks": 3,
                        "mcp_tools": len(tools),
                        "mcp_core_tools": len(core),
                        "skill_packaged": True,
                        "request_ledger_checked": True,
                        "organization_checked": True,
                        "native_app_observed": False,
                    }
                )
            )
        finally:
            if workspace.config_path.exists() and local.discover():
                local.request("POST", "/shutdown")
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with FileLock(workspace.runtime / "coordinator.lock", timeout=0):
                            break
                    except Timeout:
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "The smoke-check coordinator did not stop."
                            ) from None
                        await asyncio.sleep(0.05)
                if os.name == "nt":
                    # The coordinator releases its catalog lock before Python closes
                    # inherited log handles. Windows disallows unlinking that brief
                    # open handle; wait for actual closure in this disposable workspace.
                    while True:
                        try:
                            (workspace.runtime / "service.log").unlink(missing_ok=True)
                            break
                        except PermissionError:
                            if time.monotonic() >= deadline:
                                raise RuntimeError(
                                    "Coordinator log handle did not close."
                                ) from None
                            await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(check())
