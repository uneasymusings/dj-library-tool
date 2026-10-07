"""Request/organization adapters exercise real ASGI, catalog workers, and original tones."""

import json
from pathlib import Path

import pytest
from mcp import Client
from mutagen.id3 import TBPM, TCON, TKEY
from mutagen.wave import WAVE
from typer.testing import CliRunner

from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.mcp_server import build_server
from djlib.interfaces.tool_manifest import LIBRARY_WORKFLOW_TOOLS
from tests.test_delivery_transports import assert_envelope, finish

# Organization tools are outside the default core MCP profile.
pytestmark = pytest.mark.usefixtures("full_mcp_tools")


def catalog(http, audio_factory):
    sources = [
        audio_factory(f"library-tone-{n}.wav", frequency=220 + 110 * n, frames=8820)
        for n in range(2)
    ]
    first = WAVE(sources[0])
    first.add_tags()
    first.tags.add(TBPM(encoding=3, text="118"))
    first.tags.add(TKEY(encoding=3, text="Am"))
    first.tags.add(TCON(encoding=3, text="House"))
    first.save()
    plan = assert_envelope(
        http.post(
            "/plans",
            json={
                "name": "Original library transport tones",
                "tracks": [
                    {"path": str(source), "artist": "Test Artist", "title": f"Original tone {n}"}
                    for n, source in enumerate(sources)
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
                "idempotency_key": "catalog-library-tones",
            },
        ).json()
    )
    collection_id = finish(http, job["job_id"])["result"]["collection_id"]
    collection = assert_envelope(http.get(f"/collections/{collection_id}").json())
    return sources, collection["tracks"]


def request_body():
    return {
        "name": "Tonight request coverage",
        "idempotency_key": "transport-requests",
        "items": [
            {"artist": "Test Artist", "title": "Original tone 0"},
            {
                "kind": "unknown",
                "label": "Unknown opener ID",
                "timestamp": "01:02:03",
                "source_url": "https://www.youtube.com/watch?v=original-set",
            },
            {"artist": "Missing Artist", "title": "Missing Recording", "version": "Radio edit"},
        ],
    }


def reference(track):
    return {key: track[key] for key in ("recording_id", "asset_revision_id")}


def annotation_body(track):
    return {
        **reference(track),
        "revision": 0,
        "idempotency_key": "transport-annotation",
        "notes": "Warm-up opener",
        "genres": ["House"],
        "tags": ["warmup"],
        "set_role": "opener",
        "energy": 3,
        "bpm": {"value": 118, "source": "operator", "verified": True},
        "key": {"value": "Am", "source": "operator", "verified": True},
    }


def organization_body(tracks):
    return {
        "name": "Tonight warm-up",
        "idempotency_key": "transport-organization",
        "tracks": [reference(track) for track in reversed(tracks)],
        "filters": {"tags": ["warmup"], "bpm_min": 110, "bpm_max": 120, "require_verified": True},
        "unknown": "exclude",
        "order_by": "bpm",
    }


def assert_unchanged_catalog(http, tracks, sources, original_bytes):
    library = assert_envelope(http.get("/library", params={"limit": 20}).json())
    assert {
        (
            t["recording_id"],
            t["asset_revision_id"],
            t["sha256"],
            t["artist"],
            t["title"],
            t["version"],
        )
        for t in library["tracks"]
    } == {
        (
            t["recording_id"],
            t["asset_revision_id"],
            t["sha256"],
            t["artist"],
            t["title"],
            t["version"],
        )
        for t in tracks
    }
    assert [source.read_bytes() for source in sources] == original_bytes
    jobs = assert_envelope(http.get("/jobs").json())
    assert all(job["kind"] != "download" for job in jobs["jobs"])


async def test_all_nine_library_mcp_tools_use_real_asgi_and_catalog(
    application, library_http, audio_factory
):
    sources, tracks = catalog(library_http, audio_factory)
    original_bytes = [source.read_bytes() for source in sources]
    called = set()
    async with Client(build_server(application.workspace)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert tools.keys() >= LIBRARY_WORKFLOW_TOOLS
        for name in LIBRARY_WORKFLOW_TOOLS:
            assert tools[name].output_schema
        for name, definition in (
            ("djlib_create_request", "RequestCreate"),
            ("djlib_resolve_request", "RequestResolution"),
            ("djlib_annotate", "AnnotationRequest"),
            ("djlib_organize", "OrganizationRequest"),
        ):
            assert tools[name].input_schema["$defs"][definition]["additionalProperties"] is False

        async def call(name, arguments, error=None):
            called.add(name)
            reply = await client.call_tool(name, arguments)
            assert not reply.is_error, reply
            return assert_envelope(reply.structured_content, error)

        ledger = await call("djlib_create_request", {"request_body": request_body()})
        request_id = ledger["request_id"]
        assert ledger["counts"]["satisfied"] == 1
        assert ledger["counts"]["unknown"] == 1
        assert ledger["counts"]["missing"] == 1
        assert ledger["items"][0]["accepted"]["recording_id"] == tracks[0]["recording_id"]
        page = await call("djlib_request", {"request_id": request_id, "after": 1, "limit": 1})
        assert page["items"][0]["input"]["timestamp"] == "01:02:03"
        assert page["next_offset"] == 2
        refreshed = await call(
            "djlib_refresh_request",
            {"request_id": request_id, "revision": 1, "item_ids": [ledger["items"][1]["item_id"]]},
        )
        assert refreshed["items"][0] == ledger["items"][0]
        assert refreshed["revision"] == 1  # nothing changed, revision kept
        await call(
            "djlib_refresh_request", {"request_id": request_id, "revision": 2}, "REQUEST_STALE"
        )
        selected = await call(
            "djlib_resolve_request",
            {
                "request_id": request_id,
                "item_id": ledger["items"][2]["item_id"],
                "resolution": {
                    "revision": 1,
                    "action": "select_source",
                    "source_url": "https://soundcloud.com/test/missing-recording",
                    "notes": "Source selected only; no acquisition authorized by this operation",
                },
            },
        )
        assert selected["items"][2]["state"] == "source_selected"
        assert selected["items"][2]["accepted"] is None
        replay = await call("djlib_create_request", {"request_body": request_body()})
        assert replay.pop("reused") is True and replay == selected
        report = await call("djlib_request_report", {"request_id": request_id, "revision": 2})
        missing = json.loads(Path(report["report_path"]).read_text())
        assert missing["unresolved_items"] == 2
        assert missing["items"][0]["input"]["timestamp"] == "01:02:03"
        assert missing["automatic_acquisition"] is False
        metadata = await call("djlib_track_metadata", reference(tracks[0]))
        assert metadata["recording_id"] == tracks[0]["recording_id"]
        assert metadata["embedded"]["bpm"]["value"] == 118
        assert metadata["embedded"]["key"]["value"] == "Am"
        assert metadata["embedded"]["bpm"]["verified"] is False
        assert metadata["acoustic_analysis_performed"] is False
        before = await call("djlib_annotations", reference(tracks[0]))
        assert before["revision"] == 0
        annotation_job = await call("djlib_annotate", {"request_body": annotation_body(tracks[0])})
        annotated = annotation_job["result"]
        assert annotated["revision"] == 1
        patch_job = await call(
            "djlib_annotate",
            {
                "request_body": {
                    **reference(tracks[0]),
                    "revision": 1,
                    "idempotency_key": "transport-notes-patch",
                    "notes": "Updated notes only",
                }
            },
        )
        patched = patch_job["result"]
        assert patched["annotations"]["notes"] == "Updated notes only"
        for name in ("genres", "tags", "set_role", "energy", "bpm", "key"):
            assert patched["annotations"][name] == annotated["annotations"][name]
        fetched = await call("djlib_annotations", reference(tracks[0]))
        assert fetched["annotations"] == patched["annotations"]
        assert fetched["revision"] == 2
        assert (
            await call("djlib_annotate", {"request_body": annotation_body(tracks[0])})
            == annotation_job
        )
        await call(
            "djlib_annotate",
            {
                "request_body": {
                    **reference(tracks[0]),
                    "revision": 1,
                    "idempotency_key": "stale-new-key",
                    "notes": "Stale edit",
                }
            },
            "ANNOTATION_STALE",
        )
        await call(
            "djlib_annotate",
            {
                "request_body": {
                    **annotation_body(tracks[0]),
                    "notes": "Changed intent under reused key",
                }
            },
            "IDEMPOTENCY_CONFLICT",
        )
        organized = await call("djlib_organize", {"request_body": organization_body(tracks)})
        repeated = await call("djlib_organize", {"request_body": organization_body(tracks)})
        assert repeated["job_id"] == organized["job_id"]
        organized = finish(library_http, organized["job_id"])
        assert organized["result"]["source_modified"] is False
        assert organized["result"]["app_state"] == "not_tracked_here"
        collection = assert_envelope(
            library_http.get(f"/collections/{organized['result']['collection_id']}").json()
        )
        assert [track["recording_id"] for track in collection["tracks"]] == [
            tracks[0]["recording_id"]
        ]
    assert called == LIBRARY_WORKFLOW_TOOLS
    assert_unchanged_catalog(library_http, tracks, sources, original_bytes)


def test_library_cli_groups_emit_standard_json_and_preserve_patch_omissions(
    application, library_http, audio_factory, tmp_path
):
    sources, tracks = catalog(library_http, audio_factory)
    original_bytes = [source.read_bytes() for source in sources]
    runner = CliRunner()

    def cli(*arguments, error=None):
        result = runner.invoke(
            cli_app, ["--workspace", str(application.workspace.root), *map(str, arguments)]
        )
        assert result.exit_code == (2 if error else 0), result.output
        return assert_envelope(json.loads(result.stdout), error)

    def file(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    requested = cli("requests", "create", "--file", file("requests.json", request_body()))
    request_id = requested["request_id"]
    assert (
        cli("requests", "get", request_id, "--after", 1, "--limit", 1)["items"][0]["state"]
        == "unknown"
    )
    refreshed = cli(
        "requests",
        "refresh",
        request_id,
        "--revision",
        1,
        "--item-id",
        requested["items"][1]["item_id"],
    )
    assert refreshed["items"][0] == requested["items"][0]
    # Nothing changed, so the revision (and any printed command using it) stays valid.
    assert refreshed["revision"] == 1
    cli("requests", "refresh", request_id, "--revision", 2, error="REQUEST_STALE")
    selected = cli(
        "requests",
        "resolve",
        request_id,
        requested["items"][2]["item_id"],
        "--file",
        file(
            "resolution.json",
            {
                "revision": 1,
                "action": "select_source",
                "source_url": "https://soundcloud.com/test/selected-song",
                "notes": "No download performed",
            },
        ),
    )
    assert selected["items"][2]["accepted"] is None
    report = cli("requests", "report", request_id, "--revision", 2)
    assert Path(report["report_path"]).is_file()
    assert (
        cli("requests", "create", "--file", tmp_path / "requests.json")["request_id"] == request_id
    )
    ref = reference(tracks[0])
    metadata = cli(
        "organize", "metadata", ref["recording_id"], "--asset-revision-id", ref["asset_revision_id"]
    )
    assert metadata["recording_id"] == ref["recording_id"]
    assert (
        cli(
            "organize", "get", ref["recording_id"], "--asset-revision-id", ref["asset_revision_id"]
        )["revision"]
        == 0
    )
    annotation_job = cli(
        "organize", "annotate", "--file", file("annotation.json", annotation_body(tracks[0]))
    )
    annotated = annotation_job["result"]
    patch_job = cli(
        "organize",
        "annotate",
        "--file",
        file(
            "patch.json",
            {**ref, "revision": 1, "idempotency_key": "cli-clear-notes", "notes": None},
        ),
    )
    patched = patch_job["result"]
    assert patched["annotations"].get("notes") is None
    for name in ("genres", "tags", "set_role", "energy", "bpm", "key"):
        assert patched["annotations"][name] == annotated["annotations"][name]
    organized = cli(
        "organize", "collection", "--file", file("organization.json", organization_body(tracks))
    )
    repeated = cli("organize", "collection", "--file", tmp_path / "organization.json")
    assert repeated["job_id"] == organized["job_id"]
    organized = finish(library_http, organized["job_id"])
    collection = assert_envelope(
        library_http.get(f"/collections/{organized['result']['collection_id']}").json()
    )
    assert [track["recording_id"] for track in collection["tracks"]] == [tracks[0]["recording_id"]]
    cli(
        "requests",
        "create",
        "--file",
        file("bad-request.json", {**request_body(), "acquire_all": True}),
        error="INPUT_INVALID",
    )
    cli(
        "organize",
        "annotate",
        "--file",
        file("bad-annotation.json", {**annotation_body(tracks[0]), "analyze_acoustically": True}),
        error="INPUT_INVALID",
    )
    cli(
        "organize",
        "annotate",
        "--file",
        file(
            "stale-annotation.json",
            {**ref, "revision": 1, "idempotency_key": "cli-stale-new-key", "notes": "Old evidence"},
        ),
        error="ANNOTATION_STALE",
    )
    assert_unchanged_catalog(library_http, tracks, sources, original_bytes)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/requests", {**request_body(), "auto_download": True}),
        ("/requests/not-used/refresh", {"revision": 1, "force": True}),
        (
            "/requests/not-used/items/not-used",
            {
                "revision": 1,
                "action": "select_source",
                "source_url": "https://soundcloud.com/test/song",
                "acquired": True,
            },
        ),
        (
            "/annotations",
            {
                "recording_id": "not-used",
                "asset_revision_id": "not-used",
                "revision": 0,
                "idempotency_key": "x",
                "notes": "x",
                "native_analysis": True,
            },
        ),
        (
            "/organization",
            {
                "name": "Invalid request",
                "tracks": [{"recording_id": "not-used", "asset_revision_id": "not-used"}],
                "idempotency_key": "x",
                "guess_bpm": True,
            },
        ),
    ],
)
def test_library_http_invalid_fields_return_structured_errors(library_http, path, body):
    response = library_http.post(path, json=body)
    assert response.status_code == 422
    assert_envelope(response.json(), "INPUT_INVALID")
