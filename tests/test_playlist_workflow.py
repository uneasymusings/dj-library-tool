"""A DJ previews an exact selection, checks ownership, then follows the real export state."""

import asyncio
import csv
import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from djlib.application.playlist_import import PlaylistImport, import_playlist, preview_playlist
from djlib.domain.errors import AppError
from djlib.interfaces import desktop_workflow as desktop
from djlib.interfaces.service import create_app
from djlib.workspace import atomic_json
from tests.test_requests import owned


def exported(rows, fields=None):
    fields = fields or ["Track Name", "Artist Name(s)", "Added At", "Track URI"]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(fields)
    writer.writerows(rows)
    return stream.getvalue()


def body(text, **values):
    return PlaylistImport(name="Friday", text=text, **values)


def test_latest_selection_uses_added_timestamp_not_position_or_release_date():
    text = exported(
        [
            ["Old", "Artist", "2026-01-01T00:00:00Z", ""],
            ["Newest", "Artist", "2026-11-02T01:00:00+02:00", ""],
            ["Middle", "Artist", "2026-11-01T22:30:00Z", ""],
        ]
    )
    preview = preview_playlist(body(text, latest=2))
    assert [r["title"] for r in preview["tracks"]] == ["Newest", "Middle"]
    assert [r["source_position"] for r in preview["tracks"]] == [2, 3]
    assert preview["total_rows"] == 3 and preview["selected_rows"] == 2
    assert preview["selection"] == "latest_by_date_added"


def test_latest_200_of_a_large_playlist_are_not_the_final_200_custom_positions():
    text = exported(
        [[f"Song {i}", "Artist", f"2026-10-{1 + i % 28:02}T12:00:00Z", ""] for i in range(9498)]
    )
    preview = preview_playlist(body(text, latest=200))
    assert preview["total_rows"] == 9498
    assert len(preview["tracks"]) == 200
    assert all(row["added_at"].startswith("2026-10-28") for row in preview["tracks"])
    assert preview["tracks"][0]["source_position"] == 28


@pytest.mark.parametrize(
    "date", ["", "yesterday", "2026-10-30", "2026-10-30T12:00:00", "not a date"]
)
def test_latest_refuses_missing_invalid_or_timezone_less_dates(date):
    text = exported([["One", "Artist", date, ""]])
    with pytest.raises(AppError, match="Added At") as error:
        preview_playlist(body(text, latest=200))
    assert error.value.code == "PLAYLIST_DATES_REQUIRED"
    assert preview_playlist(body(text))["selected_rows"] == 1


def test_release_date_does_not_substitute_for_date_added():
    text = exported(
        [["One", "Artist", "2026-11-01T00:00:00Z"]],
        ["Track Name", "Artist Name(s)", "Release Date"],
    )
    with pytest.raises(AppError, match="Release dates"):
        preview_playlist(body(text, latest=1))


def test_csv_quotes_bom_unicode_multiple_artists_and_exact_mix_survive():
    text = "\ufeff" + exported(
        [["Rain, again (Extended Mix)", "Ana Belén;Other Artist", "", "spotify:track:" + "a" * 22]]
    )
    preview = preview_playlist(body(text))
    row = preview["tracks"][0]
    assert row["artist"] == "Ana Belén, Other Artist"
    assert row["title"] == "Rain, again (Extended Mix)"
    assert row["source_url"] == "https://open.spotify.com/track/" + "a" * 22


def test_duplicates_and_same_date_ties_keep_source_order():
    text = exported([["One", "Artist", "2026-10-30T00:00:00Z", ""]] * 3)
    assert [x["source_position"] for x in preview_playlist(body(text, latest=2))["tracks"]] == [
        1,
        2,
    ]
    assert len(preview_playlist(body(text))["tracks"]) == 3


@pytest.mark.parametrize(
    "text",
    [
        "Track Name,Artist Name(s)\nOne,Artist,extra",
        'Track Name,Artist Name(s)\n"One,Artist',
        "Track Name,Artist Name(s)\nOne,",
    ],
)
def test_malformed_csv_never_silently_drops_rows(text):
    with pytest.raises(AppError):
        preview_playlist(body(text))


def test_large_all_selection_and_link_only_are_actionable_errors():
    text = exported([["One", "Artist", "", ""]] * 1001)
    with pytest.raises(AppError) as error:
        preview_playlist(body(text))
    assert error.value.code == "PLAYLIST_SELECTION_REQUIRED"
    with pytest.raises(AppError) as error:
        preview_playlist(body("https://open.spotify.com/playlist/" + "b" * 22))
    assert error.value.code == "PLAYLIST_EXPORT_REQUIRED"


def test_plain_tracklists_keep_ids_and_warn_about_skipped_lines():
    result = preview_playlist(body("1. Artist - Song (Dub)\n2. ID - ID\n3. unparseable"))
    assert result["tracks"][0]["title"] == "Song (Dub)"
    assert result["tracks"][1]["kind"] == "unknown"
    assert result["warnings"]
    with pytest.raises(ValidationError):
        body("Artist - Song", latest=0)


def test_mixesdb_is_opt_in_until_maintainer_confirmation(monkeypatch):
    from djlib.sources import mixesdb

    monkeypatch.delenv("DJLIB_MIXESDB", raising=False)
    assert not mixesdb.enabled()
    for value in ("on", "1", "true", "yes"):
        monkeypatch.setenv("DJLIB_MIXESDB", value)
        assert mixesdb.enabled()
    for value in ("", "off", "false", "typo"):
        monkeypatch.setenv("DJLIB_MIXESDB", value)
        assert not mixesdb.enabled()


def test_malformed_action_record_does_not_prevent_service_startup(application):
    folder = application.workspace.runtime / "desktop-actions"
    folder.mkdir(parents=True)
    (folder / "broken.json").write_text("[]", encoding="utf-8")
    desktop.DesktopRunner(application)
    assert desktop.read_action(application.workspace, "broken") is None


def test_malformed_native_history_is_replaced_by_a_valid_receipt(application):
    from djlib.interfaces.rekordbox_cli import history_file, remember, remembered

    path = history_file(application.workspace)
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")
    remember(application.workspace, "crate", import_fingerprint="new")
    assert remembered(application.workspace)["crate"]["import_fingerprint"] == "new"
    path.write_text('{"crate": "broken"}', encoding="utf-8")
    remember(application.workspace, "crate", import_fingerprint="next")
    assert remembered(application.workspace)["crate"]["import_fingerprint"] == "next"


async def test_import_checks_same_mix_and_is_idempotent_without_downloading(
    application, audio_factory
):
    await owned(application, audio_factory, artist="Artist", title="Owned")
    text = exported([["Owned", "Artist", "", ""], ["Missing (Dub)", "Artist", "", ""]])
    first = import_playlist(application, body(text))
    second = import_playlist(application, body(text))
    assert first["request"]["request_id"] == second["request"]["request_id"]
    assert first["request"]["counts"]["satisfied"] == 1
    assert first["request"]["counts"]["missing"] == 1
    assert all(job["kind"] != "download" for job in application.jobs()["jobs"])


def test_playlist_http_routes_require_auth_and_preview_does_not_create_a_set(application):
    app = create_app(application.workspace, "playlist-test", run_worker=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        data = body("Artist - One").model_dump(mode="json")
        assert client.post("/playlists/preview", json=data).status_code == 401
        headers = {"Authorization": "Bearer " + application.workspace.token()}
        result = client.post("/playlists/preview", json=data, headers=headers)
        assert result.status_code == 200 and result.json()["result"]["selected_rows"] == 1
        assert application.saved("requests")["total"] == 0
        foreign = {**headers, "Origin": "https://evil.example"}
        assert client.post("/playlists/import", json=data, headers=foreign).status_code == 403
        created = client.post("/playlists/import", json=data, headers=headers).json()
        assert created["ok"] and created["result"]["request"]["total_items"] == 1


@pytest.fixture
def crate(application, audio_factory):
    from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
    from tests.conftest import execute

    async def make():
        path = audio_factory("workflow.wav")
        plan = application.plan(
            CollectionRequest(
                name="Tonight", tracks=[TrackInput(path=str(path), artist="Artist", title="Tone")]
            )
        )
        job = application.start(
            StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key="workflow")
        )
        result = await execute(application, job["job_id"])
        return result["result"]["collection_id"]

    return make


async def test_local_files_and_collection_membership_never_claim_usb_readiness(application, crate):
    collection_id = await crate()
    status = desktop.workflow_status(application, collection_id)
    assert status["local_files"] == status["track_count"] == 1
    assert not status["import_recorded"] and not status["usb_verified_at_last_check"]
    assert not status["hardware_playback_verified"]
    assert status["with_bpm_and_key"] == 0


async def test_terminal_receipts_share_prep_state_and_new_attempt_invalidates_old_check(
    application, crate
):
    from djlib.interfaces.rekordbox_cli import remember

    collection_id = await crate()
    fingerprint = desktop.collection_fingerprint(application.collection(collection_id))
    remember(
        application.workspace,
        collection_id,
        import_fingerprint=fingerprint,
        usb_fingerprint=fingerprint,
        usb={"playlist_on_device": True, "in_order": True, "found": 1, "expected": 1},
    )
    status = desktop.workflow_status(application, collection_id)
    assert status["import_recorded"] and status["usb_verified_at_last_check"]
    # Even a previous page receipt cannot survive a newer terminal export attempt.
    atomic_json(
        application.workspace.runtime / "desktop-actions" / f"{collection_id}.json",
        {
            "fingerprint": fingerprint,
            "state": "completed",
            "action": "usb",
            "result": {"playlist_on_device": True, "in_order": True, "found": 1, "expected": 1},
        },
    )
    remember(application.workspace, collection_id, usb=None, usb_fingerprint=fingerprint)
    status = desktop.workflow_status(application, collection_id)
    assert status["import_recorded"] and not status["usb_verified_at_last_check"]
    remember(application.workspace, collection_id, import_fingerprint="old", usb_fingerprint="old")
    atomic_json(
        application.workspace.runtime / "desktop-actions" / f"{collection_id}.json",
        {"fingerprint": "old", "state": "completed", "action": "usb"},
    )
    status = desktop.workflow_status(application, collection_id)
    assert status["previous_action_is_stale"] and not status["usb_verified_at_last_check"]


async def test_usb_evidence_requires_full_ordered_match_and_is_invalidated_by_crate_changes(
    application, crate
):
    collection_id = await crate()
    collection = application.collection(collection_id)
    path = application.workspace.runtime / "desktop-actions" / f"{collection_id}.json"
    record = {
        "fingerprint": desktop.collection_fingerprint(collection),
        "state": "completed",
        "action": "usb",
        "result": {"playlist_on_device": True, "in_order": True, "found": 1, "expected": 1},
    }
    atomic_json(path, record)
    assert desktop.workflow_status(application, collection_id)["usb_verified_at_last_check"]
    record["result"]["in_order"] = False
    atomic_json(path, record)
    assert not desktop.workflow_status(application, collection_id)["usb_verified_at_last_check"]
    record["fingerprint"] = "old crate"
    atomic_json(path, record)
    status = desktop.workflow_status(application, collection_id)
    assert status["previous_action_is_stale"] and not status["import_recorded"]


def fake_process(reply, code=0):
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_data(json.dumps(reply).encode())
    stdout.feed_eof()
    stderr.feed_data(b'{"event":"select_playlist","instruction":"Click Tonight"}\n')
    stderr.feed_data(b'{"event":"waiting","reason":"No playlist selected","playlist":"Tonight"}\n')
    stderr.feed_eof()

    async def wait():
        return code

    return SimpleNamespace(stdout=stdout, stderr=stderr, wait=wait, returncode=code)


async def test_native_runner_uses_fixed_cli_arguments_and_duplicate_clicks_reuse_action(
    application, crate, monkeypatch, tmp_path
):
    collection_id = await crate()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return fake_process(
            {
                "ok": True,
                "result": {"playlist_on_device": True, "in_order": True, "found": 1, "expected": 1},
            }
        )

    monkeypatch.setattr(desktop.asyncio, "create_subprocess_exec", spawn)
    runner = desktop.DesktopRunner(application)
    body = desktop.DesktopAction(action="usb", idempotency_key="click")
    first = await runner.start(collection_id, body)
    assert await runner.start(collection_id, body) == first
    with pytest.raises(AppError, match="already running"):
        await runner.start(
            collection_id, desktop.DesktopAction(action="import", idempotency_key="another")
        )
    await runner.task
    assert len(calls) == 1 and calls[0][-3:] == ("rekordbox", "usb", collection_id)
    status = desktop.workflow_status(application, collection_id)
    assert status["usb_verified_at_last_check"] and status["action"]["finished_at"]
    assert status["action"]["instruction"] == "Click Tonight"
    assert not runner.active


async def test_native_failure_and_restart_never_display_completed(
    application, crate, monkeypatch, tmp_path
):
    collection_id = await crate()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "darwin")

    async def spawn(*args, **kwargs):
        return fake_process({"ok": False, "error": {"message": "Unlock your Mac"}}, code=2)

    monkeypatch.setattr(desktop.asyncio, "create_subprocess_exec", spawn)
    runner = desktop.DesktopRunner(application)
    await runner.start(
        collection_id, desktop.DesktopAction(action="import", idempotency_key="failed")
    )
    await runner.task
    status = desktop.workflow_status(application, collection_id)
    assert status["action"]["state"] == "failed" and not status["import_recorded"]
    path = application.workspace.runtime / "desktop-actions" / f"{collection_id}.json"
    record = json.loads(path.read_text())
    record["state"] = "running"
    atomic_json(path, record)
    desktop.DesktopRunner(application)
    assert desktop.read_action(application.workspace, collection_id)["state"] == "interrupted"


async def test_shutdown_before_first_native_turn_releases_lock(
    application, crate, monkeypatch, tmp_path
):
    collection_id = await crate()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    runner = desktop.DesktopRunner(application)
    await runner.start(
        collection_id, desktop.DesktopAction(action="import", idempotency_key="stop")
    )
    await runner.close()
    assert not runner.active
    assert desktop.read_action(application.workspace, collection_id)["state"] == "interrupted"
    from filelock import FileLock

    with FileLock(str(tmp_path / "djlib-desktop-action.lock"), timeout=0):
        pass


async def test_read_analysis_uses_pull_and_keeps_previous_import_evidence(
    application, crate, monkeypatch, tmp_path
):
    collection_id = await crate()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return fake_process({"ok": True, "result": {"updated": 1}})

    monkeypatch.setattr(desktop.asyncio, "create_subprocess_exec", spawn)
    runner = desktop.DesktopRunner(application)
    await runner.start(collection_id, desktop.DesktopAction(action="import", idempotency_key="one"))
    await runner.task
    await runner.start(
        collection_id, desktop.DesktopAction(action="analysis", idempotency_key="two")
    )
    await runner.task
    assert calls[1][-2:] == ("rekordbox", "pull")
    status = desktop.workflow_status(application, collection_id)
    assert status["import_recorded"] and not status["usb_verified_at_last_check"]


async def test_new_usb_attempt_invalidates_previous_device_check(
    application, crate, monkeypatch, tmp_path
):
    collection_id = await crate()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "darwin")

    async def spawn(*args, **kwargs):
        return fake_process(
            {
                "ok": True,
                "result": {"playlist_on_device": True, "in_order": True, "found": 1, "expected": 1},
            }
        )

    monkeypatch.setattr(desktop.asyncio, "create_subprocess_exec", spawn)
    runner = desktop.DesktopRunner(application)
    await runner.start(collection_id, desktop.DesktopAction(action="usb", idempotency_key="one"))
    await runner.task
    assert desktop.workflow_status(application, collection_id)["usb_verified_at_last_check"]
    await runner.start(collection_id, desktop.DesktopAction(action="usb", idempotency_key="two"))
    assert not desktop.workflow_status(application, collection_id)["usb_verified_at_last_check"]
    await runner.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_desktop_ui_keeps_the_playlist_prompt_until_export_starts():
    from tests.test_review_page import run_page

    messages = run_page(
        'desktopMessage({instruction: "Click Friday (2)", '
        'event: {event: "waiting", reason: "No playlist selected"}})',
        'desktopMessage({instruction: "Click Friday (2)", '
        'event: {event: "export_started", playlist: "Friday (2)", device: "STICK"}})',
        'desktopMessage({instruction: "Click Friday (2)", '
        'event: {event: "verify_progress", done: 42, total: 56}})',
    )
    assert messages[0] == "Click Friday (2)"
    assert "is exporting" in messages[1] and "STICK" in messages[1]
    assert messages[2] == "Checking USB files: 42 of 56."


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_import_ui_requires_preview_and_invalidates_it_when_selection_changes():
    """Exercise event handlers with a tiny DOM; no browser, server or native action runs."""
    path = Path(__file__).parents[1] / "src/djlib/interfaces/web/app.js"
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("playlist_ui.cjs")), str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"
