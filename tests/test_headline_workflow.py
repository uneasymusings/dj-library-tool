"""Tracklist → owned/missing → crate → rekordbox BPM/key, through CLI, HTTP and MCP."""

import json
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest
from mcp import Client
from typer.testing import CliRunner

from djlib.application.tracklists import parse_tracklist
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.mcp_server import build_server
from djlib.interfaces.tool_manifest import PREP_TOOLS
from tests.test_delivery_transports import assert_envelope, finish


def test_tracklist_lines_become_requests_without_guessing():
    items, skipped = parse_tracklist(
        "\n".join(
            [
                "# comment",
                "01. Velvet Static - Night Bus (Extended Mix) [DEFECTED]",
                "02) Nia Okoro – Slow Burn",
                "[47:12] ID - ID",
                "ID - ID",
                "Lumen Drift — Glasshouse [Dub Version]",
                "• Ana Belén Ríos - Corazón Eléctrico",
                "just some words",
            ]
        )
    )
    named = [(i.artist, i.title) for i in items if i.kind == "named"]
    assert named == [
        ("Velvet Static", "Night Bus (Extended Mix)"),
        ("Nia Okoro", "Slow Burn"),
        ("Lumen Drift", "Glasshouse [Dub Version]"),
        ("Ana Belén Ríos", "Corazón Eléctrico"),
    ]
    unknown = [i for i in items if i.kind == "unknown"]
    assert [(i.timestamp, i.artist, i.title) for i in unknown] == [("47:12", None, None)]
    assert [(number, reason) for number, _, reason in skipped] == [
        (5, "unknown ID needs a timestamp or --source"),
        (8, "no “Artist - Title” separator"),
    ]
    headed, skipped = parse_tracklist("Set Zero — Friday\n1. A - One\nB - Two\n2. C - Three")
    assert [(i.artist, i.title) for i in headed] == [("A", "One"), ("B", "Two"), ("C", "Three")]
    assert skipped == [(1, "Set Zero — Friday", "looks like a heading")]
    bare, skipped = parse_tracklist("Fresh\n01 A - One\n2 B - Two\n03. 2 Unlimited - No Limit")
    assert [(i.artist, i.title) for i in bare] == [
        ("A", "One"),
        ("B", "Two"),
        ("2 Unlimited", "No Limit"),
    ]
    assert skipped == [(1, "Fresh", "looks like a heading")]
    copied, skipped = parse_tracklist(
        "01\nLumen - Halo\nw/ Velvet Static - Night Bus\n02\nNia - Burn"
    )
    assert [(i.artist, i.title) for i in copied] == [
        ("Lumen", "Halo"),
        ("Velvet Static", "Night Bus"),
        ("Nia", "Burn"),
    ]
    assert skipped == []
    featured, _ = parse_tracklist("Lumen - Halo [ft. Ana]\nLumen - Rain [Defected]")
    assert [i.title for i in featured] == ["Halo [ft. Ana]", "Rain"]
    gap, _ = parse_tracklist("01. A - One\n02 B - Two\n[12:30] C - Three\n04 D - Four")
    assert [i.artist for i in gap] == ["A", "B", "C", "D"]
    plain, _ = parse_tracklist("2 Unlimited - No Limit\n808 State - Pacific\nOvermono - Gem Lingo")
    assert [i.artist for i in plain] == ["2 Unlimited", "808 State", "Overmono"]
    with_source, _ = parse_tracklist("ID - ID", "https://example.com/set")
    assert with_source[0].source_url == "https://example.com/set"


def rekordbox_xml(path: Path, tracks: list[tuple[Path, str, str]]) -> Path:
    entries = "".join(
        f"<TRACK TrackID={quoteattr(str(n))} Name='t{n}' Artist='a'"
        f" Location={quoteattr(source.as_uri())} AverageBpm={quoteattr(bpm)}"
        f" Tonality={quoteattr(key)}/>"
        for n, (source, bpm, key) in enumerate(tracks, 1)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?><DJ_PLAYLISTS Version='1.0.0'>"
        "<PRODUCT Name='rekordbox' Version='7.2.3' Company='AlphaTheta'/>"
        f"<COLLECTION Entries='{len(tracks)}'>{entries}</COLLECTION>"
        "<PLAYLISTS><NODE Type='0' Name='ROOT' Count='0'/></PLAYLISTS></DJ_PLAYLISTS>",
        encoding="utf-8",
    )
    return path


def build(http, sources, labels, key="own"):
    plan = assert_envelope(
        http.post(
            "/plans",
            json={
                "name": "Owned",
                "tracks": [
                    {"path": str(source), "artist": artist, "title": title}
                    for source, (artist, title) in zip(sources, labels, strict=True)
                ],
            },
        ).json()
    )
    job = assert_envelope(
        http.post(
            "/jobs", json={"plan_id": plan["plan_id"], "revision": 1, "idempotency_key": key}
        ).json()
    )
    return finish(http, job["job_id"])


async def test_tracklist_to_crate_with_rekordbox_analysis(
    application,
    library_http,
    audio_factory,
    tmp_path,
):
    sources = [audio_factory(f"own-{n}.wav", frequency=300 + 100 * n) for n in range(3)]
    build(
        library_http,
        sources,
        [
            ("Velvet Static", "Night Bus (Extended Mix)"),
            ("Nia Okoro", "Slow Burn"),
            ("Lumen", "Dub"),
        ],
    )
    tracklist = tmp_path / "Set Zero.txt"
    tracklist.write_text(
        "1. Nia Okoro - Slow Burn\n2. Someone - Not Owned\n3. Velvet Static - Night Bus"
        " (Extended Mix)\nloose words\n",
        encoding="utf-8",
    )
    workspace = ["--workspace", str(application.workspace.root)]
    reply = CliRunner().invoke(
        cli_app, [*workspace, "requests", "create", "--text", str(tracklist)]
    )
    assert reply.exit_code == 0, reply.output
    created = json.loads(reply.stdout)
    assert created["warnings"] == ["line 4 skipped (no “Artist - Title” separator): loose words"]
    ledger = created["result"]
    assert ledger["name"] == "Set Zero"
    assert ledger["counts"]["satisfied"] == 2 and ledger["counts"]["missing"] == 1

    async with Client(build_server(application.workspace)) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert tools >= PREP_TOOLS

        async def call(name, arguments, error=None):
            reply = await client.call_tool(name, arguments)
            assert not reply.is_error, reply
            return assert_envelope(reply.structured_content, error)

        await call(
            "djlib_collect_request",
            {"request_id": ledger["request_id"], "revision": ledger["revision"] + 1},
            "REQUEST_STALE",
        )
        job = await call(
            "djlib_collect_request",
            {"request_id": ledger["request_id"], "revision": ledger["revision"]},
        )
        collection_id = finish(library_http, job["job_id"])["result"]["collection_id"]
        crate = assert_envelope(library_http.get(f"/collections/{collection_id}").json())
        assert crate["name"] == "Set Zero"
        assert [t["title"] for t in crate["tracks"]] == ["Slow Burn", "Night Bus (Extended Mix)"]

        # rekordbox analyzed two originals; the third track is not in this XML.
        xml = rekordbox_xml(
            tmp_path / "music" / "rekordbox.xml",
            [
                (sources[0], "124.00", "8A"),
                (sources[1], "118.50", "Am"),
                (tmp_path / "x.wav", "1", "C"),
            ],
        )
        imported = await call("djlib_import_rekordbox_analysis", {"path": str(xml)})
        assert imported["matched"] == imported["updated"] == 2
        assert imported["unmatched"] == 1 and imported["analysis_accuracy_verified"] is False
        again = await call("djlib_import_rekordbox_analysis", {"path": str(xml)})
        assert again["updated"] == 0 and again["unchanged"] == 2

    rows = {t["title"]: t["dj"] for t in library_http.get("/library").json()["result"]["tracks"]}
    assert rows["Night Bus (Extended Mix)"]["bpm"] == 124.0
    assert rows["Night Bus (Extended Mix)"]["key"] == "8A"
    assert rows["Slow Burn"]["bpm_source"] == "rekordbox_analysis"
    crate = assert_envelope(library_http.get(f"/collections/{collection_id}").json())
    assert {t["dj"]["key"] for t in crate["tracks"]} == {"8A", "Am"}

    # A value someone set by hand survives a re-import; the CLI copies outside XML in.
    slow = next(
        t
        for t in library_http.get("/library").json()["result"]["tracks"]
        if t["title"] == "Slow Burn"
    )
    assert_envelope(
        library_http.post(
            "/annotations",
            json={
                "recording_id": slow["recording_id"],
                "asset_revision_id": slow["asset_revision_id"],
                "revision": 1,
                "idempotency_key": "hand-bpm",
                "bpm": {"value": 119, "source": "operator", "verified": True},
            },
        ).json()
    )
    outside = rekordbox_xml(tmp_path / "Desktop" / "export.xml", [(sources[1], "120", "Am")])
    reply = CliRunner().invoke(cli_app, [*workspace, "import-rekordbox", str(outside)])
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.stdout)["result"]["kept_your_values"] == 1
    assert (
        next(
            t["dj"]["bpm"]
            for t in library_http.get("/library").json()["result"]["tracks"]
            if t["title"] == "Slow Burn"
        )
        == 119
    )

    # rekordbox values drive BPM filters for new collections.
    references = [
        {"recording_id": t["recording_id"], "asset_revision_id": t["asset_revision_id"]}
        for t in library_http.get("/library").json()["result"]["tracks"]
    ]
    job = assert_envelope(
        library_http.post(
            "/organization",
            json={
                "name": "Peak",
                "tracks": references,
                "filters": {"bpm_min": 122},
                "idempotency_key": "peak",
            },
        ).json()
    )
    peak = finish(library_http, job["job_id"])["result"]
    assert peak["selected_count"] == 1


def test_collect_needs_owned_tracks(application, library_http):
    ledger = assert_envelope(
        library_http.post(
            "/requests",
            json={
                "name": "Wishlist",
                "idempotency_key": "wish",
                "items": [{"artist": "Nobody", "title": "Nothing"}],
            },
        ).json()
    )
    reply = library_http.post(
        f"/requests/{ledger['request_id']}/collection", json={"revision": ledger["revision"]}
    ).json()
    assert_envelope(reply, "COLLECTION_EMPTY")


@pytest.mark.parametrize("arguments", [[], ["--file", "a.json", "--text", "b.txt"]])
def test_request_create_needs_exactly_one_source(application, arguments):
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "requests", "create", *arguments]
    )
    assert reply.exit_code == 2
    assert json.loads(reply.stdout)["error"]["code"] == "INPUT_INVALID"


def test_delivery_plan_from_flags(application, library_http, audio_factory):
    sources = [audio_factory("flag.wav", frequency=640)]
    collection_id = build(library_http, sources, [("Velvet Static", "Night Bus")])["result"][
        "collection_id"
    ]
    workspace = ["--workspace", str(application.workspace.root)]
    missing = CliRunner().invoke(cli_app, [*workspace, "delivery", "plan", "--collection", "x"])
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)["error"]["code"] == "INPUT_INVALID"
    reply = CliRunner().invoke(
        cli_app,
        [
            *workspace,
            "delivery",
            "plan",
            "--collection",
            collection_id,
            "--workflow",
            "serato_import",
            "--app-version",
            "3.2.1",
        ],
    )
    assert reply.exit_code == 0, reply.output
    planned = json.loads(reply.stdout)["result"]
    assert planned["request"]["name"] == "Owned (serato import)"
    assert planned["request"]["phase"] == "pilot"
    assert planned["next_step"] == "prepare_working_copies"


def test_requests_collect_uses_the_current_revision(application, library_http, audio_factory):
    build(library_http, [audio_factory("c.wav", frequency=720)], [("Velvet Static", "Night Bus")])
    ledger = assert_envelope(
        library_http.post(
            "/requests",
            json={
                "name": "Tonight",
                "idempotency_key": "tonight",
                "items": [{"artist": "Velvet Static", "title": "Night Bus"}],
            },
        ).json()
    )
    reply = CliRunner().invoke(
        cli_app,
        [
            "--workspace",
            str(application.workspace.root),
            "requests",
            "collect",
            ledger["request_id"],
            "--name",
            "Tonight crate",
        ],
    )
    assert reply.exit_code == 0, reply.output
    job = json.loads(reply.stdout)["result"]
    collection_id = finish(library_http, job["job_id"])["result"]["collection_id"]
    crate = assert_envelope(library_http.get(f"/collections/{collection_id}").json())
    assert crate["name"] == "Tonight crate" and crate["track_count"] == 1


def test_cli_keeps_checking_until_the_budget_is_no_longer_the_limit(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    from djlib.application import requests as request_module

    sources = [audio_factory(f"budget-{n}.wav", frequency=200 + 50 * n) for n in range(3)]
    build(library_http, sources, [("Velvet Static", f"Tune {n}") for n in range(3)])
    # Each refresh may hash about one file, so the CLI needs several bounded rounds.
    monkeypatch.setattr(request_module, "MAX_VERIFY_BYTES", sources[0].stat().st_size + 1)
    wanted = tmp_path / "wanted.txt"
    wanted.write_text("\n".join(f"Velvet Static - Tune {n}" for n in range(3)), encoding="utf-8")
    reply = CliRunner().invoke(
        cli_app,
        [
            "--workspace",
            str(application.workspace.root),
            "requests",
            "create",
            "--text",
            str(wanted),
        ],
    )
    assert reply.exit_code == 0, reply.output
    ledger = json.loads(reply.stdout)["result"]
    assert ledger["counts"]["satisfied"] == 3
    assert ledger["revision"] > 2


def test_rerunning_a_tracklist_rechecks_it_against_new_music(
    application, library_http, audio_factory, tmp_path
):
    build(library_http, [audio_factory("first.wav", frequency=310)], [("Lumen", "Halo")])
    tracklist = tmp_path / "Friday.txt"
    tracklist.write_text("Lumen - Halo\nLumen - Rain\n", encoding="utf-8")
    workspace = ["--workspace", str(application.workspace.root)]
    first = CliRunner().invoke(
        cli_app, [*workspace, "requests", "create", "--text", str(tracklist)]
    )
    assert json.loads(first.stdout)["result"]["counts"]["satisfied"] == 1
    # The DJ adds the missing song and runs the same tracklist again.
    build(library_http, [audio_factory("second.wav", frequency=620)], [("Lumen", "Rain")], "own-2")
    again = CliRunner().invoke(
        cli_app, [*workspace, "requests", "create", "--text", str(tracklist)]
    )
    result = json.loads(again.stdout)["result"]
    assert result["request_id"] == json.loads(first.stdout)["result"]["request_id"]
    assert result["counts"]["satisfied"] == 2
