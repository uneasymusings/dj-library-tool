"""Fresh CLI processes, actual stdio protocol, and every MCP tool's ASGI workflow."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx
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


def process_alive_diagnostic(pid):
    """Read-only observation; never use os.kill(pid, 0) on Windows."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
        if not handle:
            return {"open_process_error": ctypes.get_last_error()}
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            return {"alive": state == 258 if state in (0, 258) else None, "wait_result": state}
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return {"alive": True}
    except ProcessLookupError:
        return {"alive": False}
    except OSError as error:
        return {"alive": None, "error": type(error).__name__}


def coordinator_diagnostics(workspace, operation, value, *, expected=None):
    # Synthetic workspace logs only; never include tokens or request headers.
    try:
        with (workspace.runtime / "service.log").open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 16 * 1024))
            log_tail = stream.read(16 * 1024).decode("utf-8", errors="replace")
    except OSError as exc:
        log_tail = f"Coordinator log unavailable: {type(exc).__name__}"
    try:
        with (workspace.runtime / "service.json").open(encoding="utf-8") as stream:
            record = json.loads(stream.read(16 * 1024))
        runtime = {
            key: record.get(key)
            for key in ("pid", "instance_id", "workspace_id", "url", "protocol_version")
        }
    except (OSError, ValueError, AttributeError) as exc:
        runtime = {"unavailable": type(exc).__name__}
    diagnostics = {
        "operation": operation,
        "response": value,
        "runtime_identity": runtime,
        "coordinator_log_tail": log_tail,
    }
    # Snapshot logs/runtime before a failure-only probe. Its success is diagnostic
    # evidence, never a retry that can turn the original failure into a pass.
    if expected is not None:
        diagnostics["expected_identity"] = expected
        diagnostics["process"] = process_alive_diagnostic(expected["pid"])
        parts = urlsplit(expected["url"])
        if (
            parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or not parts.port
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
        ):
            diagnostics["direct_health"] = {"error": "InvalidPreviouslyVerifiedURL"}
            return diagnostics
        started = time.monotonic()
        health = {}
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(
                    f"{expected['url']}/health",
                    headers=LocalClient(workspace, allow_start=False).headers(),
                )
            health["status_code"] = response.status_code
            reply = json.loads(response.content[: 16 * 1024])
            result = reply.get("result") if isinstance(reply, dict) else None
            if isinstance(result, dict):
                health["result"] = {
                    key: result.get(key)
                    for key in (
                        "pid",
                        "instance_id",
                        "workspace_id",
                        "protocol_version",
                        "application_version",
                    )
                }
                health["same_identity"] = (
                    result.get("pid") == expected["pid"]
                    and result.get("instance_id") == expected["instance_id"]
                    and result.get("workspace_id") == workspace.config().workspace_id
                )
            else:
                health["error"] = "NoHealthResult"
        except (httpx.HTTPError, OSError, ValueError, AppError) as error:
            health["error"] = type(error).__name__
        health["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["direct_health"] = health
    return diagnostics


def live_identity(workspace, expected=None, *, operation="coordinator identity"):
    """Health-check without a startup path; an exited coordinator must fail this test."""
    client = LocalClient(workspace, allow_start=False)
    url = client.discover()
    # Lead with the failure reason: pytest truncates the long diagnostics below.
    assert url, (
        f"discovery failed ({client.last_discovery_error}) {operation}",
        coordinator_diagnostics(
            workspace,
            operation,
            {"discover": None, "error": client.last_discovery_error, "expected_identity": expected},
            expected=expected,
        ),
    )
    try:
        record = json.loads((workspace.runtime / "service.json").read_text())
    except (OSError, ValueError) as error:
        pytest.fail(
            str(
                coordinator_diagnostics(
                    workspace,
                    operation,
                    {"runtime_read_error": type(error).__name__, "expected_identity": expected},
                    expected=expected,
                )
            )
        )
    identity = {key: record.get(key) for key in ("instance_id", "pid", "url")}
    assert isinstance(identity["pid"], int) and identity["pid"] > 0, identity
    assert identity["instance_id"] and identity["url"] == url, identity
    try:
        health = client.request("GET", "/health")["result"]
    except AppError as error:
        pytest.fail(
            str(
                coordinator_diagnostics(
                    workspace,
                    operation,
                    {
                        "health_error": error.as_dict(),
                        "expected_identity": expected,
                    },
                    expected=expected,
                )
            )
        )
    assert (health["instance_id"], health["pid"]) == (identity["instance_id"], identity["pid"]), (
        coordinator_diagnostics(
            workspace, operation + ": runtime must match authenticated health", identity
        )
    )
    if expected is not None:
        assert identity == expected, coordinator_diagnostics(
            workspace,
            operation + ": coordinator must survive without replacement",
            {
                "observed_identity": identity,
                "expected_identity": expected,
            },
        )
    return identity


def stdio_transport(workspace):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "djlib.interfaces.cli", "--workspace", str(workspace.root), "mcp", "serve"],
        cwd=ROOT,
    )


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
        # Allow the bounded 45-second startup-lock wait and 30-second readiness
        # budget, plus interpreter startup on slower Windows runners.
        timeout=120,
    )
    assert result.returncode == expected, coordinator_diagnostics(
        workspace,
        "CLI " + " ".join(map(str, args)),
        {
            "expected_exit_code": expected,
            "actual_exit_code": result.returncode,
            "stdout_tail": result.stdout[-16 * 1024 :],
            "stderr_tail": result.stderr[-16 * 1024 :],
        },
    )
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
    if sys.platform == "win32":
        # Start from the ordinary CLI parent, outside the SDK's disposable Windows
        # subprocess job. POSIX deliberately still exercises MCP cold startup.
        assert cli(workspace, "service", "start")["ok"]
        prestarted = live_identity(workspace)
    else:
        assert LocalClient(workspace).discover() is None
        prestarted = None

    def diagnostics(operation, value):
        return coordinator_diagnostics(workspace, operation, value)

    def checked_envelope(value, operation):
        assert isinstance(value, dict), diagnostics(operation, value)
        assert value.get("ok") is True, diagnostics(operation, value)
        assert value.get("error") is None, diagnostics(operation, value)
        assert isinstance(value.get("result"), dict), diagnostics(operation, value)
        return value["result"]

    def checked_tool(reply, operation):
        assert not reply.is_error, diagnostics(operation, reply.model_dump(mode="json"))
        return checked_envelope(reply.structured_content, operation)

    transport = stdio_transport(workspace)
    async with Client(transport) as client:
        tools = (await client.list_tools()).tools
        from djlib.interfaces.tool_manifest import TOOL_NAMES

        assert {tool.name for tool in tools} == TOOL_NAMES
        assert all(t.output_schema is not None for t in tools)
        reply = await client.call_tool(
            "djlib_plan_collection",
            {
                "request_body": {
                    "name": "Stdio",
                    "tracks": [{"path": str(path), "artist": "Artist", "title": "Track"}],
                }
            },
        )
        plan = checked_tool(reply, "djlib_plan_collection")
        assert plan.get("plan_id"), diagnostics("djlib_plan_collection", reply.structured_content)
        reply = await client.call_tool(
            "djlib_start", {"plan_id": plan["plan_id"], "revision": 1, "idempotency_key": "stdio"}
        )
        started = checked_tool(reply, "djlib_start")
        assert started.get("job_id"), diagnostics("djlib_start", reply.structured_content)
        job_id = started["job_id"]
        original = live_identity(workspace, prestarted)
    # The protocol client has exited. Accepted work still belongs to the coordinator.
    # Assert before any request that could mask process death by starting another
    # daemon, then use a non-starting client for every subsequent job poll.
    live_identity(workspace, original)
    try:
        result = await asyncio.to_thread(LocalClient(workspace, allow_start=False).wait, job_id, 30)
    except AppError as error:
        pytest.fail(str(diagnostics("wait after stdio client exit", error.as_dict())))
    completed = checked_envelope(result, "wait for durable job")
    assert not completed.get("timed_out"), diagnostics("wait for durable job", result)
    assert completed.get("state") == "completed", diagnostics("wait for durable job", result)
    assert completed.get("outcome") == "complete", diagnostics("wait for durable job", result)
    async with Client(transport) as fresh:
        reply = await fresh.call_tool("djlib_job", {"job_id": job_id})
        persisted = checked_tool(reply, "djlib_job from fresh client")
        assert persisted.get("state") == "completed", diagnostics(
            "djlib_job from fresh client", reply.structured_content
        )
        assert persisted.get("outcome") == "complete", diagnostics(
            "djlib_job from fresh client", reply.structured_content
        )
    live_identity(workspace, original)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows SDK process-lifetime guard")
async def test_windows_cold_stdio_requires_external_coordinator_start(live_workspace):
    workspace = live_workspace
    workspace.initialize()
    transport = stdio_transport(workspace)
    async with Client(transport) as client:
        reply = await client.call_tool("djlib_capabilities")
        envelope = reply.structured_content
        # Application failures use our structured ok=False envelope; they need
        # not set the SDK transport-level is_error flag.
        assert isinstance(envelope, dict), coordinator_diagnostics(
            workspace, "cold Windows MCP", envelope
        )
        assert envelope["ok"] is False and envelope["result"] is None, coordinator_diagnostics(
            workspace, "cold Windows MCP", envelope
        )
        assert envelope["error"]["code"] == "COORDINATOR_START_REQUIRED"
        assert envelope["error"]["retryable"] is False
        assert LocalClient(workspace, allow_start=False).discover() is None
        assert not (workspace.runtime / "service.json").exists()
        assert not (workspace.runtime / "service.log").exists()
    assert not (workspace.runtime / "service.json").exists()
    assert cli(workspace, "service", "start")["ok"]
    original = live_identity(workspace)
    async with Client(transport) as client:
        reply = await client.call_tool("djlib_capabilities")
        envelope = reply.structured_content
        assert not reply.is_error and envelope["ok"], coordinator_diagnostics(
            workspace, "Windows MCP after external start", envelope
        )
        assert envelope["result"]["workspace_id"] == workspace.config().workspace_id
        live_identity(workspace, original)
    live_identity(workspace, original)


def test_forced_coordinator_exit_preserves_durable_review_and_accepted_job(
    live_workspace, audio_factory, tmp_path
):
    """A forced restart is distinct from survival and preserves a real worker checkpoint."""
    workspace = live_workspace
    path = audio_factory(artist="Original performer", title="Original tone")
    workspace.initialize([path.parent])
    assert cli(workspace, "service", "start")["ok"]
    original = live_identity(workspace, operation="after initial external CLI startup")
    context = {"original_identity": original}

    def checked_client_call(operation, function, *args):
        # These calls use allow_start=False. Preserve the failure, but attach
        # enough bounded state to distinguish unavailable health from replacement.
        try:
            return function(*args)
        except AppError as error:
            pytest.fail(
                str(
                    coordinator_diagnostics(
                        workspace,
                        operation,
                        {**context, "error": error.as_dict()},
                        expected=context.get("recovered_identity", original),
                    )
                )
            )

    request = tmp_path / "recovery-collection.json"
    request.write_text(
        json.dumps(
            {
                "name": "Durable review after forced exit",
                "tracks": [
                    {"path": str(path), "artist": "Conflicting performer", "title": "Other"}
                ],
            }
        )
    )
    live_identity(workspace, original, operation="before collection plan CLI")
    plan = cli(workspace, "plan", "--file", request)["result"]
    live_identity(workspace, original, operation="after plan, before job submission CLI")
    started = cli(workspace, "start", plan["plan_id"], "--revision", 1, "--key", "forced-exit")[
        "result"
    ]
    context["job_id"] = started["job_id"]
    live_identity(workspace, original, operation="after submission, before initial job wait")
    client = LocalClient(workspace, allow_start=False)
    attention = checked_client_call(
        "wait for initial review checkpoint", client.wait, started["job_id"], 30
    )["result"]
    assert attention["state"] == "needs_attention", attention
    live_identity(workspace, original, operation="after review checkpoint, before reviews CLI")
    review = cli(workspace, "reviews", "list", "--job-id", started["job_id"])["result"]["reviews"][
        0
    ]
    context["review_id"] = review["review_id"]
    live_identity(workspace, original, operation="after original reviews CLI")
    # The PID belongs to the authenticated, isolated fixture coordinator. Never
    # signal a stale runtime record or the test runner itself.
    assert original["pid"] != os.getpid()
    live_identity(workspace, original, operation="immediately before intentional termination")
    os.kill(original["pid"], signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
    wait_for_coordinator_exit(workspace)
    assert checked_client_call("discover after intentional termination", client.discover) is None
    assert cli(workspace, "service", "start")["ok"]
    recovered = live_identity(workspace, operation="after explicit recovery CLI startup")
    context["recovered_identity"] = recovered
    assert recovered["instance_id"] != original["instance_id"]
    persisted = checked_client_call(
        "read persisted job after explicit restart",
        client.request,
        "GET",
        f"/jobs/{started['job_id']}",
    )["result"]
    assert persisted["state"] == "needs_attention" and persisted["counts"] == attention["counts"]
    live_identity(
        workspace, recovered, operation="after persisted job, before restored reviews CLI"
    )
    restored = cli(workspace, "reviews", "list", "--job-id", started["job_id"])["result"]["reviews"]
    assert restored == [review]
    live_identity(workspace, recovered, operation="after restored reviews, before resolve CLI")
    cli(
        workspace,
        "reviews",
        "resolve",
        review["review_id"],
        "--revision",
        review["revision"],
        "--choice",
        "use_file_metadata",
    )
    live_identity(
        workspace, recovered, operation="after resolve CLI, before final nonstarting wait"
    )
    completed = checked_client_call(
        "wait for resolved job on recovered coordinator", client.wait, started["job_id"], 30
    )["result"]
    assert completed["state"] == "completed" and completed["outcome"] == "complete", completed
    assert completed["counts"] == {"succeeded": 1}
    live_identity(workspace, recovered, operation="after final wait, before collection CLI")
    tracks = cli(workspace, "collection", completed["result"]["collection_id"])["result"]["tracks"]
    assert len(tracks) == 1 and tracks[0]["artist"] == "Original performer"
    live_identity(workspace, recovered, operation="after recovered collection CLI")


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
            from djlib.interfaces.tool_manifest import CORE_TOOLS

            assert called == CORE_TOOLS
            # Delivery and library workflows have their own transport coverage.
