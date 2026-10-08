"""Windows MCP must reuse an externally owned coordinator, never spawn into a host job."""

from types import SimpleNamespace

import pytest
from mcp import Client

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import client as client_module
from djlib.interfaces import mcp_server
from djlib.interfaces.client import LocalClient
from tests.test_client_versions import coordinator


@pytest.mark.parametrize(("method", "path"), [("GET", "/capabilities"), ("POST", "/downloads")])
def test_no_start_client_rejects_cold_requests_without_spawn_or_runtime_writes(
    application, monkeypatch, method, path
):
    def forbidden(*args, **kwargs):
        pytest.fail("A no-start connection must not spawn or acquire a startup lock")

    monkeypatch.setattr(client_module.subprocess, "Popen", forbidden)
    monkeypatch.setattr(client_module, "FileLock", forbidden)
    before = {
        p.name: p.read_bytes() for p in application.workspace.runtime.iterdir() if p.is_file()
    }
    with pytest.raises(AppError) as error:
        LocalClient(application.workspace, allow_start=False).request(method, path, data={})
    assert error.value.code == "COORDINATOR_START_REQUIRED"
    assert error.value.status == 503
    assert error.value.retryable is False
    assert "service start" in error.value.message and "outside MCP" in error.value.message
    assert "launch.py" in error.value.message
    after = {p.name: p.read_bytes() for p in application.workspace.runtime.iterdir() if p.is_file()}
    assert after == before


def test_no_start_client_uses_authenticated_running_coordinator(application, monkeypatch):
    _, _, calls, _ = coordinator(
        application, monkeypatch, health_version=__version__, version=__version__
    )
    reply = LocalClient(application.workspace, allow_start=False).request(
        "POST", "/requests", data={"idempotency_key": "external-coordinator"}
    )
    assert reply["ok"] is True
    assert [call[:2] for call in calls] == [("GET", "/health"), ("POST", "/requests")]


def test_no_start_client_preserves_version_guard(application, monkeypatch):
    _, _, calls, _ = coordinator(application, monkeypatch, health_version="0.1.0a2")
    with pytest.raises(AppError) as error:
        LocalClient(application.workspace, allow_start=False).request("POST", "/requests", data={})
    assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
    assert [call[:2] for call in calls] == [("GET", "/health")]


def test_explicit_start_reuses_compatible_external_coordinator(application, monkeypatch):
    client, state, calls, _ = coordinator(
        application, monkeypatch, health_version=__version__, version=__version__
    )
    assert client.start() == state["url"]
    assert [call[:2] for call in calls] == [("GET", "/health")]


def test_explicit_start_rejects_old_engine_without_replacing_or_stopping_it(
    application, monkeypatch
):
    client, _, calls, _ = coordinator(application, monkeypatch)
    with pytest.raises(AppError) as error:
        client.start()
    assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
    assert "service stop" in error.value.message
    assert [call[:2] for call in calls] == [("GET", "/health"), ("GET", "/capabilities")]


def test_default_client_still_starts_one_coordinator(application, monkeypatch):
    client = LocalClient(application.workspace)
    discoveries = iter([None, "http://127.0.0.1:8181"])
    monkeypatch.setattr(client, "discover", lambda: next(discoveries))
    spawned = []
    monkeypatch.setattr(client_module.subprocess, "Popen", lambda *a, **kw: spawned.append((a, kw)))
    assert client.ensure() == "http://127.0.0.1:8181"
    assert len(spawned) == 1
    assert "djlib.interfaces.service" in spawned[0][0][0]


def simulated_startup(application, monkeypatch, *, ready_at=None, exit_code=None):
    clock = {"now": 0.0}
    process = SimpleNamespace(poll=lambda: exit_code)
    spawned = []

    def sleep(seconds):
        assert 0 < seconds <= 0.1
        clock["now"] += seconds

    def spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        return process

    monkeypatch.setattr(
        client_module, "time", SimpleNamespace(monotonic=lambda: clock["now"], sleep=sleep)
    )
    monkeypatch.setattr(client_module.subprocess, "Popen", spawn)
    client = LocalClient(application.workspace)
    monkeypatch.setattr(
        client,
        "discover",
        lambda: (
            "http://127.0.0.1:8181" if ready_at is not None and clock["now"] >= ready_at else None
        ),
    )
    return client, clock, spawned


def test_coordinator_can_become_healthy_after_fifteen_seconds(application, monkeypatch):
    client, clock, spawned = simulated_startup(application, monkeypatch, ready_at=15)
    assert client.ensure() == "http://127.0.0.1:8181"
    assert 15 <= clock["now"] < 16
    assert len(spawned) == 1


def test_exited_startup_waits_for_an_existing_primary_before_failing(application, monkeypatch):
    # The primary can own coordinator.lock before it publishes service.json.
    client, clock, spawned = simulated_startup(application, monkeypatch, ready_at=15, exit_code=1)
    assert not (application.workspace.runtime / "service.json").exists()
    assert client.ensure() == "http://127.0.0.1:8181"
    assert 15 <= clock["now"] < 16
    assert len(spawned) == 1


@pytest.mark.parametrize(
    ("exit_code", "code", "diagnostic"),
    [
        (7, "SERVICE_START_FAILED", "exited with code 7"),
        (None, "SERVICE_START_TIMEOUT", "still running"),
    ],
)
def test_startup_failure_distinguishes_exit_from_timeout_without_exposing_logs(
    application, monkeypatch, exit_code, code, diagnostic
):
    secret = "PRIVATE_COORDINATOR_DIAGNOSTIC_DO_NOT_ECHO"
    (application.workspace.runtime / "service.log").write_text(secret, encoding="utf-8")
    client, clock, spawned = simulated_startup(application, monkeypatch, exit_code=exit_code)
    with pytest.raises(AppError) as error:
        client.ensure()
    assert error.value.code == code
    assert error.value.retryable is True
    assert diagnostic in error.value.message and "30 seconds" in error.value.message
    assert secret not in error.value.message
    assert clock["now"] == pytest.approx(30)
    assert len(spawned) == 1


def test_startup_lock_timeout_is_typed_and_never_spawns(application, monkeypatch):
    def busy(path, *, timeout):
        assert timeout == 45
        raise client_module.FileLockTimeout(str(path))

    def forbidden(*args, **kwargs):
        pytest.fail("A busy startup lock must not cause another spawn")

    monkeypatch.setattr(client_module, "FileLock", busy)
    monkeypatch.setattr(client_module.subprocess, "Popen", forbidden)
    with pytest.raises(AppError) as error:
        LocalClient(application.workspace).ensure()
    assert error.value.code == "SERVICE_START_BUSY"
    assert error.value.retryable is True
    assert error.value.status == 503
    assert not (application.workspace.runtime / "service.log").exists()


async def test_windows_mcp_cold_start_returns_typed_envelope(application, monkeypatch):
    monkeypatch.setattr(mcp_server, "sys", SimpleNamespace(platform="win32"))

    def forbidden(*args, **kwargs):
        pytest.fail("Windows MCP cold start must not create a coordinator")

    monkeypatch.setattr(client_module.subprocess, "Popen", forbidden)
    async with Client(mcp_server.build_server(application.workspace)) as client:
        reply = await client.call_tool("djlib_capabilities", {})
    assert not reply.is_error
    envelope = reply.structured_content
    assert envelope["ok"] is False and envelope["result"] is None
    assert envelope["error"]["code"] == "COORDINATOR_START_REQUIRED"


@pytest.mark.parametrize(
    ("platform", "allow_start"), [("linux", True), ("darwin", True), ("win32", False)]
)
def test_mcp_start_policy_is_platform_specific(application, monkeypatch, platform, allow_start):
    observed = []

    def local_client(workspace, **options):
        observed.append((workspace, options))
        return LocalClient(workspace, **options)

    monkeypatch.setattr(mcp_server, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(mcp_server, "LocalClient", local_client)
    mcp_server.build_server(application.workspace)
    assert observed == [
        (
            application.workspace,
            {"allow_start": allow_start, "outdated_fix": mcp_server.RECONNECT},
        )
    ]
