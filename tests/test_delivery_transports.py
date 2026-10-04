"""Delivery CLI/MCP contracts traverse real ASGI, workers, and generated audio.

Only local coordinator discovery is replaced. No native app, physical USB,
download provider, or external network is needed to test the delivery boundary.
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from djlib.domain.errors import AppError
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.mcp_server import build_server
from djlib.interfaces.service import create_app

DELIVERY_TOOLS = {
    "djlib_delivery_targets",
    "djlib_plan_delivery",
    "djlib_delivery",
    "djlib_prepare_delivery",
    "djlib_bind_delivery_device",
    "djlib_observe_delivery",
    "djlib_verify_delivery_device",
}
DECODERS_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def assert_envelope(reply, error=None):
    assert set(reply) == {"schema_version", "ok", "request_id", "result", "warnings", "error"}
    assert reply["schema_version"] == "1"
    assert reply["request_id"].startswith("req_")
    assert reply["warnings"] == []
    assert reply["ok"] is (error is None), reply
    if error:
        assert reply["result"] is None
        assert set(reply["error"]) == {"code", "message", "retryable"}
        assert reply["error"]["code"] == error, reply
        assert isinstance(reply["error"]["message"], str)
        assert isinstance(reply["error"]["retryable"], bool)
    else:
        assert reply["error"] is None
        assert isinstance(reply["result"], dict)
    return reply["result"]


@pytest.fixture
def delivery_http(application, monkeypatch):
    app = create_app(application.workspace, "delivery-transports")
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + application.workspace.token()},
    ) as http:

        def local_request(self, method, path, *, data=None, params=None):
            response = http.request(method, path, json=data, params=params)
            reply = response.json()
            if not reply["ok"]:
                error = reply["error"]
                raise AppError(
                    error["code"], error["message"], response.status_code, error["retryable"]
                )
            return reply

        monkeypatch.setattr(LocalClient, "request", local_request)
        yield http


def finish(http, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = assert_envelope(http.get(f"/jobs/{job_id}").json())
        if job["state"] not in {"queued", "running"}:
            assert job["state"] == "completed", job
            assert job["outcome"] == "complete", job
            return job
        time.sleep(0.02)
    pytest.fail(f"Generated-audio job did not finish: {job_id}")


def catalog_tone(http, audio_factory):
    source = audio_factory("delivery-transport.wav", frequency=330)
    plan = assert_envelope(
        http.post(
            "/plans",
            json={
                "name": "Original transport tone",
                "tracks": [
                    {"path": str(source), "artist": "Test Artist", "title": "Original tone"}
                ],
            },
        ).json()
    )
    job = assert_envelope(
        http.post(
            "/jobs",
            json={
                "plan_id": plan["plan_id"],
                "revision": plan["revision"],
                "idempotency_key": "delivery-transports-original-tone",
            },
        ).json()
    )
    return source, finish(http, job["job_id"])["result"]["collection_id"]


def request_body(collection_id):
    return {
        "name": "Native workflow pilot",
        "collection_ids": [collection_id],
        "workflow": "rekordbox_usb",
        "hardware_profile": "cdj-2000nxs",
        "app_version": "transport-test-version",
        "audio_mode": "wav16_44100",
        "phase": "pilot",
        "pilot_size": 1,
    }


def observation(manifest, revision):
    return {
        "revision": revision,
        "stage": "analyzed",
        "app_version": "transport-test-version",
        "track_count": manifest["unique_track_count"],
        "playlist_counts": manifest["playlist_counts"],
        "checked_recording_ids": [t["recording_id"] for t in manifest["tracks"]],
        "observer": "Transport fixture",
        "notes": "Intentionally out of order: import has not been observed.",
        "method": "native_app_ui",
        "outcome": "passed",
    }


def prepared_manifest(job, source, original_bytes):
    manifest = json.loads(Path(job["result"]["manifest_path"]).read_text())
    assert manifest["app_state"] == "prepared_for_import"
    assert manifest["device_state"] == "not_exported"
    assert manifest["unique_track_count"] == 1
    assert len(manifest["tracks"]) == 1
    working = Path(manifest["tracks"][0]["path"])
    assert working.is_file() and working != source
    assert source.read_bytes() == original_bytes
    assert Path(manifest["playlists"][0]["path"]).is_file()
    return manifest


@pytest.mark.skipif(
    not DECODERS_AVAILABLE, reason="Real delivery preparation requires FFmpeg/ffprobe"
)
async def test_all_seven_delivery_mcp_tools_through_asgi(
    application, delivery_http, audio_factory, tmp_path
):
    source, collection_id = catalog_tone(delivery_http, audio_factory)
    original_bytes = source.read_bytes()
    called = set()
    async with Client(build_server(application.workspace)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert tools.keys() >= DELIVERY_TOOLS
        assert all(tools[name].output_schema for name in DELIVERY_TOOLS)
        plan_schema = tools["djlib_plan_delivery"].input_schema
        definition = plan_schema["$defs"]["DeliveryRequest"]
        assert definition["additionalProperties"] is False
        assert definition["properties"]["phase"]["enum"] == ["pilot", "full"]
        observe_schema = tools["djlib_observe_delivery"].input_schema
        assert observe_schema["$defs"]["DeliveryObservation"]["additionalProperties"] is False

        async def call(name, arguments=None, error=None):
            called.add(name)
            reply = await client.call_tool(name, arguments or {})
            # Domain errors remain the published structured envelope, not opaque MCP failures.
            assert not reply.is_error, reply
            return assert_envelope(reply.structured_content, error)

        targets = await call("djlib_delivery_targets")
        assert "cdj-2000nxs" in targets["targets"]
        assert targets["native_automation_available"] is False
        delivery = await call("djlib_plan_delivery", {"request_body": request_body(collection_id)})
        delivery_id = delivery["delivery_id"]
        assert delivery["request"]["phase"] == "pilot"
        assert delivery["snapshot"]["selected_unique_tracks"] == 1
        assert delivery["ready_for_departure"] is False
        assert "prepare_working_copies" in delivery["blockers"]
        fetched = await call("djlib_delivery", {"delivery_id": delivery_id})
        assert fetched["snapshot"] == delivery["snapshot"]
        job = await call(
            "djlib_prepare_delivery",
            {
                "delivery_id": delivery_id,
                "revision": fetched["revision"],
                "idempotency_key": "mcp-native-pilot",
            },
        )
        completed = await asyncio.to_thread(finish, delivery_http, job["job_id"])
        manifest = prepared_manifest(completed, source, original_bytes)
        fetched = await call("djlib_delivery", {"delivery_id": delivery_id})
        assert fetched["prepared_for_import"] is True
        assert fetched["ready_for_departure"] is False
        assert "imported" in fetched["blockers"]
        revision = fetched["revision"]
        await call(
            "djlib_bind_delivery_device",
            {
                "delivery_id": delivery_id,
                "revision": revision,
                "path": str(tmp_path),
            },
            error="DEVICE_NOT_MOUNTED",
        )
        await call(
            "djlib_observe_delivery",
            {
                "delivery_id": delivery_id,
                "observation": observation(manifest, revision),
            },
            error="STAGE_REQUIRED",
        )
        await call(
            "djlib_verify_delivery_device",
            {
                "delivery_id": delivery_id,
                "revision": revision,
            },
            error="NATIVE_EXPORT_REQUIRED",
        )
        unchanged = await call("djlib_delivery", {"delivery_id": delivery_id})
        assert unchanged["revision"] == revision
        assert unchanged["evidence"] == {}
        assert unchanged["ready_for_departure"] is False
    assert called == DELIVERY_TOOLS


@pytest.mark.skipif(
    not DECODERS_AVAILABLE, reason="Real delivery preparation requires FFmpeg/ffprobe"
)
def test_delivery_cli_commands_use_real_asgi_and_worker(
    application, delivery_http, audio_factory, tmp_path
):
    source, collection_id = catalog_tone(delivery_http, audio_factory)
    original_bytes = source.read_bytes()
    runner = CliRunner()

    def cli(*args, error=None):
        reply = runner.invoke(
            cli_app,
            [
                "--workspace",
                str(application.workspace.root),
                "delivery",
                *map(str, args),
            ],
        )
        assert reply.exit_code == (2 if error else 0), reply.output
        return assert_envelope(json.loads(reply.stdout), error)

    assert cli("targets")["native_automation_available"] is False
    plan_file = tmp_path / "delivery.json"
    plan_file.write_text(json.dumps(request_body(collection_id)))
    planned = cli("plan", "--file", plan_file)
    delivery_id = planned["delivery_id"]
    fetched = cli("get", delivery_id)
    assert fetched["snapshot"] == planned["snapshot"]
    job = cli("prepare", delivery_id, "--revision", fetched["revision"], "--key", "cli-pilot")
    completed = finish(delivery_http, job["job_id"])
    manifest = prepared_manifest(completed, source, original_bytes)
    fetched = cli("get", delivery_id)
    assert fetched["prepared_for_import"] is True
    assert fetched["ready_for_departure"] is False
    revision = fetched["revision"]
    repeated = cli("prepare", delivery_id, "--revision", revision, "--key", "cli-pilot")
    assert repeated["job_id"] == job["job_id"]
    cli("bind-device", delivery_id, tmp_path, "--revision", revision, error="DEVICE_NOT_MOUNTED")
    observations_file = tmp_path / "observation.json"
    observations_file.write_text(json.dumps(observation(manifest, revision)))
    cli("observe", delivery_id, "--file", observations_file, error="STAGE_REQUIRED")
    cli("verify-device", delivery_id, "--revision", revision, error="NATIVE_EXPORT_REQUIRED")
    assert cli("get", delivery_id)["evidence"] == {}


@pytest.mark.parametrize(
    "path,body",
    [
        ("/deliveries", {**request_body("collection-not-used"), "ready_for_departure": True}),
        ("/deliveries", {**request_body("collection-not-used"), "phase": "already_exported"}),
        ("/deliveries/not-used/prepare", {"revision": 1, "idempotency_key": "x", "force": True}),
        ("/deliveries/not-used/verify", {"revision": 1, "hardware_verified": True}),
        (
            "/deliveries/not-used/observations",
            {
                "revision": 1,
                "stage": "downloaded",
                "app_version": "test",
                "track_count": 0,
                "playlist_counts": {},
                "checked_recording_ids": [],
                "observer": "test",
                "notes": "Unknown stages must be rejected",
                "method": "native_app_ui",
                "outcome": "passed",
            },
        ),
    ],
)
def test_delivery_http_rejects_unknown_fields_and_invalid_phases(delivery_http, path, body):
    response = delivery_http.post(path, json=body)
    assert response.status_code == 422
    assert_envelope(response.json(), "INPUT_INVALID")


@pytest.mark.parametrize(
    "override",
    [
        {"phase": "already_exported"},
        {"native_export_verified": True},
        {"pilot_size": 500},
    ],
)
def test_delivery_cli_rejects_unpublished_input(application, delivery_http, tmp_path, override):
    path = tmp_path / "bad-delivery.json"
    path.write_text(json.dumps({**request_body("collection-not-used"), **override}))
    result = CliRunner().invoke(
        cli_app,
        [
            "--workspace",
            str(application.workspace.root),
            "delivery",
            "plan",
            "--file",
            str(path),
        ],
    )
    assert result.exit_code == 2, result.output
    assert_envelope(json.loads(result.stdout), "INPUT_INVALID")
