"""Whole-catalog pagination and session rediscovery through real transport adapters."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mutagen.id3 import TBPM
from mutagen.wave import WAVE
from typer.testing import CliRunner

from djlib.audio.inspection import checksum
from djlib.domain.contracts import recording_key
from djlib.domain.errors import AppError
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.mcp_server import build_server
from djlib.interfaces.service import create_app
from djlib.interfaces.tool_manifest import DISCOVERY_TOOLS, TOOL_NAMES
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Collection,
    Delivery,
    FileLocation,
    Job,
    Membership,
    Recording,
)
from djlib.persistence.request_models import RequestLedger
from tests.test_delivery_transports import assert_envelope, catalog_tone, finish


def seed_catalog(app, count=105, prefix="fixture"):
    with app.db.transaction() as session:
        for index in range(count):
            identifier = f"{prefix}-{index:04}"
            session.add(
                Recording(
                    id=identifier,
                    identity_key=recording_key(prefix, str(index)),
                    artist=prefix,
                    title=str(index),
                    version="",
                    evidence={"kind": "synthetic_fixture"},
                )
            )
        session.flush()
        for index in range(count):
            identifier = f"{prefix}-{index:04}"
            session.add(
                Asset(
                    id=f"asset-{identifier}",
                    recording_id=identifier,
                    provenance={"kind": "synthetic_fixture"},
                )
            )
        session.flush()
        for index in range(count):
            identifier = f"{prefix}-{index:04}"
            session.add(
                AssetRevision(
                    id=f"rev-{identifier}",
                    asset_id=f"asset-{identifier}",
                    sha256=hashlib.sha256(identifier.encode()).hexdigest(),
                    properties={},
                )
            )
        session.flush()
        for index in range(count):
            identifier = f"{prefix}-{index:04}"
            session.add(
                FileLocation(
                    id=f"location-{identifier}",
                    revision_id=f"rev-{identifier}",
                    path=str(app.workspace.root / f"{identifier}.wav"),
                    managed=False,
                )
            )


def test_library_pages_distinct_revisions_despite_many_duplicate_locations(application):
    seed_catalog(application)
    with application.db.transaction() as session:
        for index in range(150):
            session.add(
                FileLocation(
                    id=f"extra-{index}",
                    revision_id="rev-fixture-0000",
                    path=str(application.workspace.root / f"duplicate-{index}.wav"),
                    managed=index == 0,
                )
            )
    first = application.library(limit=100)
    assert first["total"] == 105
    assert len(first["tracks"]) == len({t["recording_id"] for t in first["tracks"]}) == 100
    assert first["tracks"][0]["location_count"] == 151
    assert len(first["tracks"][0]["locations"]) == 151
    assert first["tracks"][0]["managed"] is True
    assert first["next_cursor"]
    seed_catalog(application, 1, "later")
    second = application.library(limit=100, after=first["next_cursor"])
    assert second["total"] == 105
    assert len(second["tracks"]) == 5
    assert second["next_cursor"] is None
    ids = [t["recording_id"] for t in first["tracks"] + second["tracks"]]
    assert len(ids) == len(set(ids)) == 105
    assert application.library(limit=100)["total"] == 106
    with pytest.raises(AppError, match="same listing"):
        application.library("different query", after=first["next_cursor"])
    with pytest.raises(AppError, match="same listing"):
        application.saved("collections", after=first["next_cursor"])


@pytest.mark.parametrize("cursor", ["not-a-cursor", "e30", "W10", "a" * 2001, 10])
def test_bad_cursor_is_a_typed_error(application, cursor):
    with pytest.raises(AppError) as error:
        application.library(after=cursor)
    assert error.value.code == "CURSOR_INVALID"


def seed_saved(app, kind, count=12):
    with app.db.transaction() as session:
        for index in range(count):
            common = {"id": f"{kind}-{index:03}", "name": f"Saved tonight {index:03}"}
            if kind == "collections":
                value = Collection(**common)
            elif kind == "requests":
                value = RequestLedger(**common, request={}, items=[{"kind": "unknown"}])
            elif kind == "deliveries":
                value = Delivery(
                    id=common["id"],
                    request={"name": common["name"], "workflow": "serato_import", "phase": "pilot"},
                    snapshot={},
                    evidence={"imported": {"outcome": "passed"}, "analyzed": {"outcome": "passed"}},
                )
            else:
                value = Job(
                    id=common["id"],
                    kind="collection",
                    state="completed",
                    outcome="complete",
                    request={"name": common["name"]},
                    result={},
                )
            session.add(value)


@pytest.mark.parametrize("kind", ["collections", "requests", "deliveries", "jobs"])
def test_saved_work_can_be_found_and_paged_with_honest_native_state(application, kind):
    seed_saved(application, kind)
    listing = application.jobs if kind == "jobs" else lambda **kw: application.saved(kind, **kw)
    first = listing(query="tonight", limit=7)
    second = listing(query="tonight", limit=7, after=first["next_cursor"])
    assert first["total"] == second["total"] == 12
    assert len(first[kind]) == 7 and len(second[kind]) == 5
    assert second["next_cursor"] is None
    key = {
        "collections": "collection_id",
        "requests": "request_id",
        "deliveries": "delivery_id",
        "jobs": "job_id",
    }[kind]
    assert len({row[key] for row in first[kind] + second[kind]}) == 12
    if kind == "collections":
        assert first[kind][0]["app_state"] == first[kind][0]["device_state"] == "not_tracked_here"
    if kind == "deliveries":
        assert first[kind][0]["imported"] == "passed"
        assert first[kind][0]["fresh_verification_performed"] is False
        assert "ready_for_app_use" not in first[kind][0]


def test_historical_collection_members_remain_visible_when_location_is_retired(application):
    seed_catalog(application, 1)
    with application.db.transaction() as session:
        session.add(Collection(id="historical", name="Historical"))
        session.flush()
        session.add(
            Membership(
                id="membership",
                collection_id="historical",
                recording_id="fixture-0000",
                revision_id="rev-fixture-0000",
                position=0,
            )
        )
        session.delete(session.get(FileLocation, "location-fixture-0000"))
        revision = session.get(AssetRevision, "rev-fixture-0000")
        revision.properties = {"last_known_path": "/historical/original.wav"}
    collection = application.collection("historical")
    assert len(collection["tracks"]) == 1
    assert collection["tracks"][0]["path"] is None
    assert collection["tracks"][0]["last_known_path"] == "/historical/original.wav"
    assert collection["tracks"][0]["location_availability"] == "no_recorded_location"
    with pytest.raises(AppError) as error:
        application.export("historical", "historical-export")
    assert error.value.code == "FILE_UNAVAILABLE"
    assert application.library()["total"] == 1


@pytest.fixture
def discovery_http(application, monkeypatch):
    with TestClient(
        create_app(application.workspace, "discovery-test"),
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


def test_http_and_cli_complete_catalog_and_saved_listings(application, discovery_http, tmp_path):
    seed_catalog(application)
    for kind in ("collections", "requests", "deliveries", "jobs"):
        seed_saved(application, kind)
    runner = CliRunner()

    def cli(*arguments):
        reply = runner.invoke(cli_app, ["--workspace", str(application.workspace.root), *arguments])
        assert reply.exit_code == 0, reply.output
        return assert_envelope(json.loads(reply.output))

    first = cli("library", "--limit", "100")
    second = cli("library", "--limit", "100", "--after", first["next_cursor"])
    assert len(first["tracks"]) + len(second["tracks"]) == first["total"] == 105
    for kind, arguments in (
        ("collections", ["collections", "--query", "Saved"]),
        ("requests", ["requests", "list", "--query", "Saved"]),
        ("deliveries", ["delivery", "list", "--query", "Saved"]),
        ("jobs", ["jobs", "list", "--query", "Saved"]),
    ):
        page = cli(*arguments, "--limit", "7")
        assert page["total"] == 12
        tail = assert_envelope(
            discovery_http.get(
                f"/{kind}", params={"query": "Saved", "limit": 7, "after": page["next_cursor"]}
            ).json()
        )
        assert len(tail[kind]) == 5
    extra = tmp_path / "explicitly-allowed"
    extra.mkdir()
    before = cli("roots", "list")
    added = cli("roots", "add", str(extra))
    assert added["added"] == [str(extra)]
    assert added["allowed_roots"] == before["allowed_roots"] + [str(extra)]


def test_validation_reports_fields_and_safe_reasons_without_private_input(
    application, discovery_http, tmp_path
):
    secret = "PRIVATE_INPUT_DO_NOT_ECHO\nPRIVATE_SUFFIX"
    body = {
        "name": "Validation",
        "tracks": [{"path": "/placeholder.wav", "title": secret}],
        "extra_option": secret,
    }
    response = discovery_http.post("/plans", json=body)
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "tracks.0.artist: required field" in message
    assert "tracks.0.title:" in message
    assert "extra_option: unexpected field" in message
    assert "PRIVATE_INPUT_DO_NOT_ECHO" not in response.text
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "plan", "--file", str(path)]
    )
    assert reply.exit_code == 2
    error = json.loads(reply.output)["error"]
    assert error["code"] == "INPUT_INVALID"
    assert "tracks.0.artist: required field" in error["message"]
    assert "PRIVATE_INPUT_DO_NOT_ECHO" not in reply.output


@pytest.mark.usefixtures("full_mcp_tools")
async def test_mcp_validation_preserves_safe_structured_errors(application, discovery_http):
    secret = "PRIVATE_INPUT_DO_NOT_ECHO\nPRIVATE_SUFFIX"
    async with Client(build_server(application.workspace)) as client:
        reply = await client.call_tool(
            "djlib_plan_collection",
            {
                "request_body": {
                    "name": "Validation",
                    "tracks": [{"path": "/placeholder.wav", "title": secret}],
                    "extra_option": secret,
                }
            },
        )
        assert reply.is_error is True
        assert_envelope(reply.structured_content, "INPUT_INVALID")
        message = reply.structured_content["error"]["message"]
        assert "tracks.0.artist: required field" in message
        assert "extra_option: unexpected field" in message
        assert "PRIVATE_INPUT_DO_NOT_ECHO" not in reply.model_dump_json()


@pytest.mark.usefixtures("full_mcp_tools")
async def test_new_mcp_tools_and_reconciliation_use_real_http_worker(
    application, discovery_http, audio_factory, tmp_path
):
    source, collection_id = catalog_tone(discovery_http, audio_factory)
    track = assert_envelope(discovery_http.get(f"/collections/{collection_id}").json())["tracks"][0]
    for kind in ("requests", "deliveries"):
        seed_saved(application, kind, 1)
    extra = tmp_path / "mcp-permission"
    extra.mkdir()
    async with Client(build_server(application.workspace)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == TOOL_NAMES
        called = set()

        async def call(name, arguments=None):
            called.add(name)
            reply = await client.call_tool(name, arguments or {})
            assert not reply.is_error, reply
            return assert_envelope(reply.structured_content)

        assert (await call("djlib_collections"))["total"] == 1
        assert (await call("djlib_requests", {"query": "Saved"}))["total"] == 1
        listed = await call("djlib_deliveries")
        assert listed["deliveries"][0]["fresh_verification_performed"] is False
        prior = await call("djlib_roots")
        updated = await call("djlib_add_roots", {"request_body": {"paths": [str(extra)]}})
        assert updated["allowed_roots"] == prior["allowed_roots"] + [str(extra)]
        tagged = WAVE(source)
        tagged.add_tags()
        tagged.tags.add(TBPM(encoding=3, text="123"))
        tagged.save()
        body = {
            "items": [
                {
                    "path": str(source),
                    "expected_asset_revision_id": track["asset_revision_id"],
                    "expected_sha256": checksum(source),
                    "action": "tag_only",
                }
            ],
            "idempotency_key": "mcp-reconcile",
        }
        accepted = await call("djlib_reconcile", {"request_body": body})
        completed = finish(discovery_http, accepted["job_id"])
        assert completed["kind"] == "reconcile"
        assert completed["counts"] == {"succeeded": 1}
        repeated = await call("djlib_reconcile", {"request_body": body})
        assert repeated["job_id"] == accepted["job_id"]
        assert called == DISCOVERY_TOOLS
        for tool_name, schema_name in (
            ("djlib_add_roots", "RootsRequest"),
            ("djlib_reconcile", "ReconcileRequest"),
        ):
            assert (
                tools[tool_name].input_schema["$defs"][schema_name]["additionalProperties"] is False
            )
