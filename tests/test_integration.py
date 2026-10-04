"""Fresh CLI processes, actual stdio protocol, and every MCP tool's ASGI workflow."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from djlib.domain.errors import AppError
from djlib.interfaces.client import LocalClient
from djlib.interfaces.mcp_server import build_server
from djlib.interfaces.service import create_app
from djlib.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent


def cli(workspace, *args, expected=0):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "djlib.interfaces.cli",
            "--workspace",
            str(workspace.root),
            *map(str, args),
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def live_workspace(tmp_path):
    workspace = Workspace(tmp_path / "live")
    yield workspace
    if workspace.config_path.exists() and LocalClient(workspace).discover():
        LocalClient(workspace).request("POST", "/shutdown")
        wait_for_coordinator_exit(workspace)


def wait_for_coordinator_exit(workspace):
    # A shutdown acknowledgement precedes process exit. Wait for ownership to
    # end so fixture cleanup cannot submit a second request to an exiting server.
    with FileLock(workspace.runtime / "coordinator.lock", timeout=10):
        pass


def test_cli_demo_and_all_local_commands(live_workspace, tmp_path):
    workspace = live_workspace
    demo = cli(workspace, "demo")["result"]
    assert demo["ingestion"]["outcome"] == "complete"
    assert demo["export"]["outcome"] == "complete"
    assert len(cli(workspace, "library")["result"]["tracks"]) == 3
    for command in ("capabilities", "doctor", "version", "schemas"):
        assert cli(workspace, command)["ok"]
    job_id = demo["ingestion"]["job_id"]
    collection_id = demo["ingestion"]["result"]["collection_id"]
    for args in (
        ("jobs", "list"),
        ("jobs", "get", job_id),
        ("jobs", "items", job_id),
        ("jobs", "events", job_id),
        ("jobs", "wait", job_id),
        ("reviews", "list"),
        ("collection", collection_id),
        ("usb-preflight", tmp_path),
        ("service", "status"),
    ):
        assert cli(workspace, *args)["ok"]
    reply = cli(workspace, "source-inspect", "https://evil.test", expected=2)
    assert reply["error"]["code"] == "SOURCE_UNSUPPORTED"
    assert cli(workspace, "service", "stop")["ok"]
    wait_for_coordinator_exit(workspace)


def test_cli_input_error_is_json(live_workspace, tmp_path):
    workspace = live_workspace
    cli(workspace, "init")
    file = tmp_path / "bad.json"
    file.write_text("not json")
    assert cli(workspace, "plan", "--file", file, expected=2)["error"]["code"] == "INPUT_INVALID"


def test_multiple_clients_share_single_coordinator(live_workspace):
    workspace = live_workspace
    workspace.initialize()
    with ThreadPoolExecutor(max_workers=5) as pool:
        replies = list(pool.map(lambda _: cli(workspace, "capabilities"), range(5)))
    assert len({r["result"]["workspace_id"] for r in replies}) == 1
    first = json.loads((workspace.runtime / "service.json").read_text())
    assert cli(workspace, "capabilities")["ok"]
    assert (
        json.loads((workspace.runtime / "service.json").read_text())["instance_id"]
        == first["instance_id"]
    )


async def test_actual_stdio_fresh_process(live_workspace, audio_factory):
    workspace = live_workspace
    path = audio_factory()
    workspace.initialize([path.parent])
    transport = StdioServerParameters(
        command=sys.executable,
        args=["-m", "djlib.interfaces.cli", "--workspace", str(workspace.root), "mcp", "serve"],
        cwd=ROOT,
    )
    async with Client(transport) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 23 and all(t.output_schema is not None for t in tools)
        reply = await client.call_tool(
            "djlib_plan_collection",
            {
                "request_body": {
                    "name": "Stdio",
                    "tracks": [{"path": str(path), "artist": "Artist", "title": "Track"}],
                }
            },
        )
        assert not reply.is_error
        plan = reply.structured_content["result"]
        reply = await client.call_tool(
            "djlib_start", {"plan_id": plan["plan_id"], "revision": 1, "idempotency_key": "stdio"}
        )
        job_id = reply.structured_content["result"]["job_id"]
    # The protocol client has exited. Accepted work still belongs to the coordinator.
    result = await asyncio.to_thread(LocalClient(workspace).wait, job_id, 5)
    assert result["result"]["outcome"] == "complete"
    async with Client(transport) as fresh:
        reply = await fresh.call_tool("djlib_job", {"job_id": job_id})
        assert reply.structured_content["result"]["state"] == "completed"


async def test_all_mcp_tools_through_http(application, audio_factory, monkeypatch, tmp_path):
    """Network-independent provider doubles; real MCP/ASGI/worker/catalog contracts."""
    import djlib.interfaces.service as service_module
    import djlib.jobs.worker as worker_module

    source = audio_factory("selected.wav", frequency=550)
    conflict = audio_factory("conflict.wav", artist="Other artist", title="Other title")

    async def fake_download(url, destination):
        import shutil

        destination.mkdir(parents=True)
        path = destination / "selected.wav"
        shutil.copyfile(source, path)
        return path, {"kind": "provider_fixture", "source_url": url, "source_quality": "unverified"}

    async def fake_inspect(url):
        return {
            "url": url,
            "title": "Published set",
            "chapters": [],
            "identity_evidence": "publisher metadata",
        }

    monkeypatch.setattr(worker_module, "download", fake_download)
    monkeypatch.setattr(service_module, "inspect_source", fake_inspect)
    app = create_app(application.workspace, "mcp-http")
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + application.workspace.token()},
    ) as http:

        def local_request(self, method, path, *, data=None, params=None):
            reply = http.request(method, path, json=data, params=params)
            result = reply.json()
            if not result["ok"]:
                error = result["error"]
                raise AppError(
                    error["code"], error["message"], reply.status_code, error["retryable"]
                )
            return result

        monkeypatch.setattr(LocalClient, "request", local_request)
        async with Client(build_server(application.workspace)) as client:
            called = set()

            async def call(name, args=None):
                called.add(name)
                reply = await client.call_tool(name, args or {})
                assert not reply.is_error, reply
                assert reply.structured_content["ok"], reply
                return reply.structured_content["result"]

            async def finish(job_id, expected="completed"):
                async with asyncio.timeout(10):
                    while True:
                        job = await call("djlib_job", {"job_id": job_id})
                        if job["state"] not in {"queued", "running"}:
                            assert job["state"] == expected, job
                            return job
                        await asyncio.sleep(0.02)

            await call("djlib_capabilities")
            plan = await call(
                "djlib_plan_collection",
                {
                    "request_body": {
                        "name": "MCP review",
                        "tracks": [
                            {"path": str(conflict), "artist": "Requested", "title": "Requested"}
                        ],
                    }
                },
            )
            job = await call(
                "djlib_start",
                {"plan_id": plan["plan_id"], "revision": 1, "idempotency_key": "mcp-start"},
            )
            await finish(job["job_id"], "needs_attention")
            reviews = await call("djlib_reviews", {"job_id": job["job_id"]})
            review = reviews["reviews"][0]
            await call(
                "djlib_resolve",
                {"review_id": review["review_id"], "revision": 1, "choice": "use_file_metadata"},
            )
            finished = await finish(job["job_id"])
            collection_id = finished["result"]["collection_id"]
            await call("djlib_items", {"job_id": job["job_id"]})
            await call("djlib_jobs")
            await call("djlib_library", {"query": "Other"})
            await call("djlib_collection", {"collection_id": collection_id})
            export = await call(
                "djlib_export", {"collection_id": collection_id, "idempotency_key": "mcp-export"}
            )
            assert (await finish(export["job_id"]))["result"]["track_count"] == 1
            await call("djlib_usb_preflight", {"path": str(tmp_path)})
            await call("djlib_source_inspect", {"url": "https://youtu.be/fixture"})
            download = await call(
                "djlib_download",
                {
                    "request_body": {
                        "name": "Selected",
                        "idempotency_key": "mcp-download",
                        "tracks": [
                            {
                                "url": "https://youtu.be/fixture",
                                "artist": "Test",
                                "title": "Selection",
                            }
                        ],
                    }
                },
            )
            assert (await finish(download["job_id"]))["counts"] == {"succeeded": 1}
            scan = await call(
                "djlib_scan", {"path": str(source.parent), "idempotency_key": "mcp-scan"}
            )
            await call("djlib_control", {"job_id": scan["job_id"], "action": "cancel"})
            assert called == {
                t.name
                for t in (await client.list_tools()).tools
                if t.name
                not in {
                    "djlib_delivery_targets",
                    "djlib_plan_delivery",
                    "djlib_delivery",
                    "djlib_prepare_delivery",
                    "djlib_bind_delivery_device",
                    "djlib_observe_delivery",
                    "djlib_verify_delivery_device",
                }
            }
            # New delivery tools have their own HTTP/MCP integration coverage.
