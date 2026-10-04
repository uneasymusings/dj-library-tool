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
from djlib.interfaces.client import LocalClient
from djlib.workspace import Workspace


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
            transport = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "djlib.interfaces.cli",
                    "--workspace",
                    str(workspace.root),
                    "mcp",
                    "serve",
                ],
                cwd=root,
            )
            async with Client(transport) as client:
                tools = (await client.list_tools()).tools
                assert len(tools) == 23 and all(tool.output_schema for tool in tools)
                reply = await client.call_tool("djlib_capabilities", {})
                assert reply.structured_content["ok"]
                reply = await client.call_tool("djlib_delivery_targets", {})
                assert "cdj-2000nxs" in reply.structured_content["result"]["targets"]
                if shutil.which("ffmpeg") and shutil.which("ffprobe"):
                    planned = await client.call_tool(
                        "djlib_plan_delivery",
                        {
                            "request_body": {
                                "name": "Installed delivery pilot",
                                "workflow": "serato_portable",
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
            print(json.dumps({"ok": True, "tracks": 3, "mcp_tools": 23, "skill_packaged": True}))
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
