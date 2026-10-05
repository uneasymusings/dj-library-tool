"""Version negotiation never sends an operation to an incompatible existing coordinator."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import client as client_module
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import create_app

ABSENT = object()


def coordinator(application, monkeypatch, *, health_version=ABSENT, version="0.1.0a2"):
    workspace = application.workspace
    state = {
        "url": "http://127.0.0.1:8181",
        "instance_id": "instance-test",
        "workspace_id": workspace.config().workspace_id,
        "protocol_version": "1",
        "health_version": health_version,
        "version": version,
    }
    calls = []

    def record():
        (workspace.runtime / "service.json").write_text(
            json.dumps(
                {
                    key: state[key]
                    for key in ("url", "instance_id", "workspace_id", "protocol_version")
                }
            )
        )

    record()
    original_client = httpx.Client

    def handler(request):
        assert request.url.host == "127.0.0.1"
        assert request.headers["Authorization"] == f"Bearer {workspace.token()}"
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/health":
            result = {
                key: state[key] for key in ("instance_id", "workspace_id", "protocol_version")
            }
            if state["health_version"] is not ABSENT:
                result["application_version"] = state["health_version"]
        elif request.url.path == "/capabilities":
            if state.get("capabilities_timeout"):
                raise httpx.ReadTimeout("synthetic version lookup timeout", request=request)
            result = {"workspace_id": state.get("capabilities_workspace", state["workspace_id"])}
            if state["version"] is not ABSENT:
                result["application_version"] = state["version"]
        elif request.url.path == "/shutdown":
            result = {"state": "stopping"}
        else:
            result = {"handled": request.url.path}
        return httpx.Response(
            200,
            json={
                "schema_version": "1",
                "ok": True,
                "request_id": "req-test",
                "result": result,
                "warnings": [],
                "error": None,
            },
        )

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(client_module.httpx, "Client", client)

    def forbidden_start(*args, **kwargs):
        pytest.fail("A discovered old coordinator must not be automatically replaced")

    monkeypatch.setattr(client_module.subprocess, "Popen", forbidden_start)
    return LocalClient(workspace), state, calls, record


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/library"),
        ("GET", "/jobs/accepted-job"),
        ("POST", "/requests"),
        ("POST", "/downloads"),
        ("POST", "/deliveries"),
    ],
)
def test_old_public_health_uses_authenticated_capabilities_and_blocks_operations(
    application, monkeypatch, method, path
):
    client, state, calls, _ = coordinator(application, monkeypatch)
    assert client.discover() == state["url"]
    assert [call[1] for call in calls] == ["/health"]
    for _ in range(2):
        with pytest.raises(AppError) as error:
            client.request(method, path, data={"idempotency_key": "never-submit"})
        assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
        assert error.value.status == 409
        assert error.value.retryable is False
        assert "0.1.0a2" in error.value.message
        assert __version__ in error.value.message
        assert "service stop" in error.value.message
        assert "Accepted jobs remain stored" in error.value.message
    assert sum(call[1] == "/capabilities" for call in calls) == 1
    assert all(call[0] == "GET" and call[1] in {"/health", "/capabilities"} for call in calls)


@pytest.mark.parametrize("health_version", [ABSENT, __version__])
def test_matching_version_sends_each_operation_once_and_caches_version(
    application, monkeypatch, health_version
):
    client, _, calls, _ = coordinator(
        application, monkeypatch, health_version=health_version, version=__version__
    )
    assert client.request("POST", "/requests", data={"idempotency_key": "exactly-once"})["ok"]
    assert client.request("GET", "/library")["ok"]
    assert sum(call[1] == "/requests" for call in calls) == 1
    assert sum(call[1] == "/library" for call in calls) == 1
    assert sum(call[1] == "/capabilities" for call in calls) == (
        1 if health_version is ABSENT else 0
    )
    posted = next(call for call in calls if call[1] == "/requests")
    assert json.loads(posted[2]) == {"idempotency_key": "exactly-once"}


def test_health_version_takes_precedence_without_extra_capabilities_lookup(
    application, monkeypatch
):
    client, _, calls, _ = coordinator(
        application, monkeypatch, health_version="0.1.0a3.dev0", version=__version__
    )
    with pytest.raises(AppError) as error:
        client.request("GET", "/library")
    assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
    assert "0.1.0a3.dev0" in error.value.message
    assert [call[1] for call in calls] == ["/health"]


@pytest.mark.parametrize("version", [ABSENT, None, "", "private diagnostic\nsecret", 123])
def test_unknown_server_version_fails_closed_with_typed_error(application, monkeypatch, version):
    client, _, calls, _ = coordinator(application, monkeypatch, version=version)
    with pytest.raises(AppError) as error:
        client.request("POST", "/requests", data={})
    assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
    assert "unknown application version" in error.value.message
    assert "secret" not in error.value.message
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.parametrize("failure", ["timeout", "wrong-workspace"])
def test_failed_version_fallback_cannot_authorize_mutation(application, monkeypatch, failure):
    client, state, calls, _ = coordinator(application, monkeypatch, version=__version__)
    if failure == "timeout":
        state["capabilities_timeout"] = True
    else:
        state["capabilities_workspace"] = "other-workspace"
    with pytest.raises(AppError) as error:
        client.request("POST", "/requests", data={})
    assert error.value.code == "COORDINATOR_VERSION_MISMATCH"
    assert all(call[0] == "GET" for call in calls)


def test_admin_reads_and_explicit_shutdown_remain_available_to_old_server(application, monkeypatch):
    client, _, calls, _ = coordinator(application, monkeypatch)
    assert client.request("GET", "/health")["ok"]
    capabilities = client.request("GET", "/capabilities")
    assert capabilities["result"]["application_version"] == "0.1.0a2"
    assert client.request("POST", "/shutdown")["result"]["state"] == "stopping"
    assert [call[1] for call in calls if call[0] == "POST"] == ["/shutdown"]
    assert sum(call[1] == "/capabilities" for call in calls) == 1


def test_new_discovery_instance_clears_a_cached_old_version(application, monkeypatch):
    client, state, calls, record = coordinator(application, monkeypatch)
    with pytest.raises(AppError):
        client.request("GET", "/library")
    state.update(instance_id="instance-restarted", version=__version__)
    record()
    assert client.request("GET", "/library")["ok"]
    assert sum(call[1] == "/capabilities" for call in calls) == 2
    assert sum(call[1] == "/library" for call in calls) == 1


@pytest.mark.parametrize("field", ["instance_id", "workspace_id", "protocol_version"])
def test_discovery_still_rejects_identity_or_protocol_mismatch(application, monkeypatch, field):
    client, state, calls, _ = coordinator(application, monkeypatch)
    # Keep the on-disk record unchanged while the health identity diverges.
    state[field] = "different"
    assert client.discover() is None
    assert [call[1] for call in calls] == ["/health"]


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8181",
        "http://example.test:8181",
        "http://localhost:8181",
        "http://user:password@127.0.0.1:8181",
        "http://127.0.0.1:8181/not-root",
    ],
)
def test_discovery_never_sends_token_to_a_noncanonical_loopback_endpoint(
    application, monkeypatch, url
):
    client, state, calls, record = coordinator(application, monkeypatch)
    state["url"] = url
    record()
    assert client.discover() is None
    assert calls == []


def test_cli_reports_mismatch_as_json_and_service_status_stop_still_work(application, monkeypatch):
    _, _, calls, _ = coordinator(application, monkeypatch)
    runner = CliRunner()
    prefix = ["--workspace", str(application.workspace.root)]
    blocked = runner.invoke(cli_app, [*prefix, "library"])
    assert blocked.exit_code == 2
    assert json.loads(blocked.stdout)["error"]["code"] == "COORDINATOR_VERSION_MISMATCH"
    status = runner.invoke(cli_app, [*prefix, "service", "status"])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["result"]["url"] == "http://127.0.0.1:8181"
    capabilities = runner.invoke(cli_app, [*prefix, "capabilities"])
    assert capabilities.exit_code == 0
    assert json.loads(capabilities.stdout)["result"]["application_version"] == "0.1.0a2"
    stopped = runner.invoke(cli_app, [*prefix, "service", "stop"])
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout)["result"]["state"] == "stopping"
    assert [call[1] for call in calls if call[0] == "POST"] == ["/shutdown"]


def test_current_health_publishes_exact_application_version(application):
    app = create_app(application.workspace, "version-health", run_worker=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(
            "/health", headers={"Authorization": "Bearer " + application.workspace.token()}
        )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["application_version"] == __version__
    assert result["workspace_id"] == application.workspace.config().workspace_id
    assert result["instance_id"] == "version-health"
