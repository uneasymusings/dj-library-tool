"""Terminal presentation: readable views for people, the JSON contract for everything else."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import cli, guided, terminal, views
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import create_app


def sized(width: int, **options) -> terminal.Terminal:
    """A human terminal at a fixed width, printing to the captured stdout/stderr."""
    term = terminal.Terminal(json=False, **options)
    term.out = Console(theme=terminal.THEME, highlight=False, width=width)
    term.err = Console(theme=terminal.THEME, highlight=False, width=width, stderr=True)
    return term


def invoke(*arguments, pretty=False, workspace=None):
    env = {"DJLIB_OUTPUT": "pretty" if pretty else None, "COLUMNS": "120"}
    prefix = ["--workspace", str(workspace)] if workspace is not None else []
    return CliRunner(env=env).invoke(cli_app, [*prefix, *arguments])


@pytest.fixture
def local_http(application, monkeypatch):
    """Route the CLI client to an in-process coordinator with a running worker."""
    with TestClient(
        create_app(application.workspace, "terminal-test"),
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
        yield application.workspace


def test_captured_output_keeps_the_json_envelope():
    reply = invoke("version")
    assert reply.exit_code == 0
    assert json.loads(reply.stdout)["result"] == {"version": __version__}


def test_terminal_output_is_readable_and_json_flag_restores_envelope():
    reply = invoke("version", pretty=True)
    assert reply.exit_code == 0
    assert "djlib" in reply.stdout and __version__ in reply.stdout
    with pytest.raises(ValueError):
        json.loads(reply.stdout)
    forced = CliRunner(env={"DJLIB_OUTPUT": "pretty"}).invoke(cli_app, ["--json", "version"])
    assert json.loads(forced.stdout)["ok"] is True


def test_json_flag_is_accepted_after_the_subcommand(monkeypatch, capsys):
    monkeypatch.setenv("DJLIB_OUTPUT", "pretty")
    monkeypatch.setattr(sys, "argv", ["djlib", "version", "--json"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["result"]["version"] == __version__


def test_output_mode_follows_flag_environment_then_tty(monkeypatch):
    monkeypatch.delenv(terminal.OUTPUT_ENV, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert terminal.wants_json(False) is False
    assert terminal.wants_json(True) is True
    monkeypatch.setenv(terminal.OUTPUT_ENV, "json")
    assert terminal.wants_json(False) is True
    monkeypatch.setenv(terminal.OUTPUT_ENV, "pretty")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    assert terminal.wants_json(False) is False


def test_pretty_errors_use_stderr_with_a_recovery_hint(tmp_path):
    reply = invoke("library", pretty=True, workspace=tmp_path / "missing")
    assert reply.exit_code == 2
    assert reply.stdout == ""
    assert "djlib isn't set up yet" in reply.stderr
    assert "(WORKSPACE_REQUIRED)" in reply.stderr
    assert "init --allow-root PATH" in reply.stderr
    captured = invoke("library", workspace=tmp_path / "missing")
    assert json.loads(captured.stdout)["error"]["code"] == "WORKSPACE_REQUIRED"


def test_scripts_must_supply_submission_keys(tmp_path):
    reply = invoke("export", "collection_x", workspace=tmp_path / "ws")
    assert reply.exit_code == 2
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "INPUT_INVALID" and "--key" in error["message"]


def test_scripts_and_assistants_can_scan_without_a_key(local_http):
    # `djlib scan` straight after `djlib init` is the first thing an assistant or the
    # installer runs; a rescan only reads new bytes, so it needs no retry token.
    reply = invoke("scan", workspace=local_http.root)
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.stdout)["ok"] is True


def test_cli_resolves_relative_paths_before_the_coordinator_sees_them(
    application, monkeypatch, tmp_path
):
    sent = []

    def capture(self, method, path, *, data=None, params=None):
        sent.append(data)
        return {"ok": True, "result": {"path": data["path"]}}

    monkeypatch.setattr(LocalClient, "request", capture)
    monkeypatch.chdir(tmp_path)
    reply = invoke("usb-preflight", "music", workspace=application.workspace.root)
    assert reply.exit_code == 0, reply.output
    assert sent[0]["path"] == str(tmp_path / "music")


def test_terminal_scan_defaults_to_the_only_root_and_follows_the_job(local_http, audio_factory):
    audio_factory("second.wav", frequency=330, artist="Velvet Static", title="Night Bus")
    scanned = invoke("scan", pretty=True, workspace=local_http.root)
    assert scanned.exit_code == 0, scanned.output
    assert "2 tracks indexed" in scanned.stdout
    assert "Job " not in scanned.stdout and "Updated" not in scanned.stdout  # nothing failed
    assert "library" in scanned.stdout  # next step

    listed = invoke("library", pretty=True, workspace=local_http.root)
    assert listed.exit_code == 0, listed.output
    assert "Velvet Static" in listed.stdout and "Night Bus" in listed.stdout
    assert "WAV 44.1k/16" in listed.stdout
    assert "2 of 2 tracks" in listed.stdout

    jobs = invoke("jobs", "list", pretty=True, workspace=local_http.root)
    assert "done" in jobs.stdout and "Scan" in jobs.stdout


def test_scan_without_path_indexes_every_root(local_http, audio_factory, tmp_path, monkeypatch):
    import shutil

    other, unplugged = tmp_path / "other", tmp_path / "unplugged"
    other.mkdir()
    unplugged.mkdir()
    audio_factory("../other/b.wav", frequency=330, artist="Lumen", title="Halo")
    local_http.add_roots([other, unplugged])
    unplugged.rmdir()
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    reply = invoke("scan", "--key", "k", workspace=local_http.root)
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    scans = envelope["result"]["scans"]
    assert len({scan["job_id"] for scan in scans}) == len(scans) == 2
    assert envelope["warnings"] == [f"Skipped {unplugged}: it isn't there (unplugged drive?)."]
    # The same retry token gives the same jobs back, one per folder, even after the
    # missing drive returns.
    unplugged.mkdir()
    again = json.loads(invoke("scan", "--key", "k", workspace=local_http.root).stdout)
    assert [s["job_id"] for s in again["result"]["scans"]][:2] == [s["job_id"] for s in scans]
    assert len(again["result"]["scans"]) == 3 and again["warnings"] == []
    unplugged.rmdir()

    pretty = invoke("scan", pretty=True, workspace=local_http.root)
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout.count("track indexed") == 2
    assert "unplugged drive?" in pretty.stderr


def test_scan_and_init_warn_when_ffmpeg_is_missing(local_http, tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "platform", "linux")
    reply = json.loads(invoke("scan", "--key", "w", workspace=local_http.root).stdout)
    assert reply["ok"] is True and reply["result"]["job_id"]
    assert reply["result"]["idempotency_key"] == "w"  # one folder keeps the token as given
    assert reply["warnings"] == [
        "ffmpeg and ffprobe not found, so only WAV files can be read. "
        "Install FFmpeg: sudo apt install ffmpeg"
    ]
    (tmp_path / "crate").mkdir()
    created = invoke("init", "--allow-root", str(tmp_path / "crate"), workspace=tmp_path / "new")
    assert "Install FFmpeg" in json.loads(created.stdout)["warnings"][0]
    pretty = invoke("scan", pretty=True, workspace=local_http.root)
    assert "Install FFmpeg: sudo apt install ffmpeg" in pretty.stderr


@pytest.mark.parametrize("name", sorted(views.VIEWS))
def test_every_view_tolerates_sparse_results(name):
    term = terminal.Terminal(json=False)
    views.VIEWS[name](term, {})


def test_generic_view_renders_nested_results_without_noise(capsys):
    term = terminal.Terminal(json=False)
    views.generic(
        term,
        {
            "schema_version": "1",
            "delivery_id": "delivery_x",
            "ready": True,
            "path": "/tmp/a.m3u8",
            "rows": [{"name": "A", "count": 1}],
            "nested": {"inner": "value"},
        },
    )
    out = capsys.readouterr().out
    assert "Delivery ID" in out and "delivery_x" in out and "Inner" in out
    assert "schema" not in out.lower()


def test_formatting_helpers():
    assert terminal.humanize("rekordbox_xml_handoff") == "rekordbox XML handoff"
    assert terminal.humanize("usb_space_preflight") == "USB space preflight"
    assert terminal.duration(412) == "6:52" and terminal.duration(3725) == "1:02:05"
    assert terminal.size(1_500) == "1.5 KB" and terminal.size(12) == "12 B"
    if os.name != "nt":
        assert terminal.shell_path(Path.home() / "music") == "~/music"
        assert terminal.shell_path(Path.home() / "my music") == f"'{Path.home()}/my music'"
    assert (
        views.audio_format(
            {
                "path": "a.flac",
                "properties": {"codec": "flac", "sample_rate": 44100, "bit_depth": 24},
            }
        )
        == "FLAC 44.1k/24"
    )
    assert (
        views.audio_format({"path": "a.mp3", "properties": {"codec": "mp3", "bitrate_bps": 320000}})
        == "MP3 320k"
    )


def test_command_hints_include_a_non_default_workspace(tmp_path):
    term = terminal.Terminal(json=False, workspace=tmp_path / "ws")
    assert term.command("library") == f"djlib --workspace {tmp_path / 'ws'} library"
    assert terminal.Terminal(json=False).command("scan", "my music") == "djlib scan 'my music'" or (
        os.name == "nt"
    )


@pytest.mark.parametrize(
    ("job", "kwargs", "code"),
    [
        ({"state": "completed", "outcome": "complete"}, {}, None),
        ({"state": "completed", "outcome": "completed_with_gaps"}, {}, 4),
        ({"state": "needs_attention"}, {}, 4),
        ({"state": "paused"}, {}, 4),
        ({"state": "running"}, {"timed_out": True}, 3),
        ({"state": "running"}, {"detached": True}, 130),
    ],
)
def test_followed_jobs_mirror_wait_exit_codes(job, kwargs, code):
    if code is None:
        views.exit_for_job(job, **kwargs)
        return
    with pytest.raises(typer.Exit) as exited:
        views.exit_for_job(job, **kwargs)
    assert exited.value.exit_code == code


def test_guided_observation_requires_an_interactive_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(AppError) as error:
        guided.observe(terminal.Terminal(json=False), None, "delivery_x", None)
    assert error.value.code == "INPUT_INVALID"


class FakeClient:
    def __init__(self, delivery):
        self.delivery = delivery

    def request(self, method, path, **_):
        return {"ok": True, "result": self.delivery}


def guided_delivery(tmp_path, next_step="imported"):
    manifest = tmp_path / "delivery-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"recording_id": "rec_a", "artist": "A", "title": "One"},
                    {"recording_id": "rec_b", "artist": "B", "title": "Two"},
                ],
                "playlists": [{"collection_id": "col", "name": "Warm-up", "track_count": 2}],
                "playlist_counts": {"col": 2},
                "unique_track_count": 2,
            }
        )
    )
    return {
        "delivery_id": "delivery_x",
        "revision": 3,
        "next_step": next_step,
        "request": {"name": "Pilot", "workflow": "serato_import", "app_version": "3.2.1"},
        "preparation_job": {"state": "completed", "result": {"manifest_path": str(manifest)}},
    }


def answer(monkeypatch, confirms, prompts, integers=()):
    confirms, prompts, integers = list(confirms), list(prompts), list(integers)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(guided.Confirm, "ask", lambda *a, **k: confirms.pop(0))
    monkeypatch.setattr(guided.Prompt, "ask", lambda *a, default="", **k: prompts.pop(0) or default)
    monkeypatch.setattr(guided.IntPrompt, "ask", lambda *a, **k: integers.pop(0))


def test_guided_pass_fills_counts_and_ids_from_the_frozen_manifest(monkeypatch, tmp_path):
    answer(monkeypatch, [True, True], ["", "Sam", ""])
    observation = guided.observe(
        terminal.Terminal(json=False), FakeClient(guided_delivery(tmp_path)), "delivery_x", None
    )
    assert observation.stage == "imported" and observation.outcome == "passed"
    assert observation.revision == 3 and observation.app_version == "3.2.1"
    assert observation.track_count == 2 and observation.playlist_counts == {"col": 2}
    assert observation.checked_recording_ids == ["rec_a", "rec_b"]
    assert observation.observer == "Sam" and observation.method == "native_app_ui"
    assert "Serato" in observation.notes


def test_guided_failure_records_only_what_was_seen(monkeypatch, tmp_path):
    answer(monkeypatch, [False, True], ["", "Sam", "Second track missing"], integers=[1])
    observation = guided.observe(
        terminal.Terminal(json=False), FakeClient(guided_delivery(tmp_path)), "delivery_x", None
    )
    assert observation.outcome == "failed"
    assert observation.track_count == 1 and observation.playlist_counts == {"col": 1}
    assert observation.checked_recording_ids == []
    assert observation.notes == "Second track missing"


def test_guided_observation_refuses_unready_or_hardware_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    term = terminal.Terminal(json=False)
    for step, code in (
        ("prepare_working_copies", "STAGE_REQUIRED"),
        ("hardware_playback", "INPUT_INVALID"),
    ):
        with pytest.raises(AppError) as error:
            guided.observe(term, FakeClient(guided_delivery(tmp_path, step)), "delivery_x", None)
        assert error.value.code == code
    answer(monkeypatch, [True, False], ["", "Sam", ""])
    with pytest.raises(AppError) as declined:
        guided.observe(term, FakeClient(guided_delivery(tmp_path)), "delivery_x", None)
    assert declined.value.message == "Nothing was recorded."


def test_first_workspace_becomes_the_default(tmp_path):
    from djlib.interfaces.client import default_workspace

    (tmp_path / "music").mkdir()
    first = invoke("init", "--allow-root", str(tmp_path / "music"), workspace=tmp_path / "lib")
    assert json.loads(first.stdout)["result"]["default_workspace"] is True
    assert default_workspace() == (tmp_path / "lib").resolve()
    second = invoke("init", workspace=tmp_path / "other")
    assert json.loads(second.stdout)["result"]["default_workspace"] is False
    assert default_workspace() == (tmp_path / "lib").resolve()  # the first one stays

    switched = json.loads(invoke("use", str(tmp_path / "other")).stdout)["result"]
    assert switched["source"] == "remembered" and switched["changed"] is True
    assert default_workspace() == (tmp_path / "other").resolve()
    missing = invoke("use", str(tmp_path / "nowhere"))
    assert json.loads(missing.stdout)["error"]["code"] == "WORKSPACE_REQUIRED"
    assert default_workspace() == (tmp_path / "other").resolve()


def test_status_summarizes_library_requests_and_crates(local_http, audio_factory, monkeypatch):
    from djlib.interfaces import rekordbox_cli

    audio_factory("status.wav", frequency=640, artist="Velvet Static", title="Night Bus")
    assert invoke("scan", "--key", "s", workspace=local_http.root).exit_code == 0
    for _ in range(100):
        jobs = json.loads(invoke("jobs", "list", workspace=local_http.root).stdout)["result"]
        if all(job["state"] not in {"queued", "running"} for job in jobs["jobs"]):
            break
        time.sleep(0.05)
    monkeypatch.setattr(rekordbox_cli, "rekordbox_playlists", lambda: {"Elsewhere"})
    reply = invoke("status", workspace=local_http.root)
    summary = json.loads(reply.stdout)["result"]
    assert summary["tracks"] == 2 and summary["active_jobs"] == 0
    assert summary["rekordbox_checked"] is True
    pretty = invoke("status", pretty=True, workspace=local_http.root)
    assert "2 tracks" in pretty.stdout and "BPM for 0" in pretty.stdout


def test_usb_and_set_views_show_what_came_back_from_the_stick(capsys):
    term = sized(160)
    usb = {
        "playlist": "Friday",
        "device": "/Volumes/RICARDO_AM",
        "expected": 5,
        "found": 5,
        "in_order": True,
        "playlist_on_device": True,
        "library_updated": True,
        "analysis_from_device": {"matched": 5, "updated": 2},
    }
    views.VIEWS["rekordbox usb"](term, usb)
    views.VIEWS["set"](term, {"name": "Friday", "songs": 6, "owned": 5, "usb": usb})
    out = capsys.readouterr().out
    assert out.count("5 of 5 in the player's library, in order and byte for byte") == 2
    assert out.count("5 tracks read from the stick (2 new in your catalog)") == 2


# -- polish: errors, wrapping, tables, next steps -----------------------------------------------

COLLECTION = "collection_b3dbc1726d094e3cbb85f208a04b50b7"
REQUEST = "request_83978f59196a4656ad61f846e0e8645c"
# Printed commands use the first 8 hex characters (djlib.interfaces.handles accepts them).
SHORT_COLLECTION, SHORT_REQUEST = COLLECTION[11:19], REQUEST[8:16]
JOB = "job_ee06f031562d4276b630af8f9466ef12"


def render(name, result, *, width=80, warnings=(), capsys=None):
    term = sized(width)
    views.show(term, name, {"ok": True, "result": result, "warnings": list(warnings)})
    return capsys.readouterr().out


def indents(text: str) -> list[int]:
    return [len(line) - len(line.lstrip(" ")) for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("code", "title"),
    [
        ("INPUT_INVALID", "That input didn't work"),
        ("TRANSPORT_UNCERTAIN", "Lost contact with djlib's background service"),
        ("COORDINATOR_VERSION_MISMATCH", "djlib was updated"),
        ("CLIENT_OUTDATED", "A newer djlib is running"),
        ("APP_SELECTION_TIMEOUT", "You didn't pick the playlist in time"),
        ("DEVICE_REQUIRED", "Which USB?"),
        ("SOURCE_BROWSER_ONLY", "Open this one in your browser"),
        ("TRACKLIST_NOT_FOUND", "No tracklist on that page"),
        ("DEPENDENCY_REQUIRED", "Something needs installing"),
        ("SOME_NEW_CODE", "Some new code"),
    ],
)
def test_errors_lead_with_a_plain_title_and_keep_the_code_dim(capsys, code, title):
    terminal.render_error(sized(120), {"code": code, "message": "Details."})
    first = capsys.readouterr().err.splitlines()[0]
    assert title in first and first.endswith(f"({code})")


def test_accessibility_error_says_how_to_open_the_setting(capsys):
    term = sized(80)
    terminal.render_error(
        term,
        {
            "code": "APP_AUTOMATION_NOT_ALLOWED",
            "message": "Allow your terminal app under System Settings > Privacy & Security > "
            "Accessibility, then retry.",
        },
    )
    err = capsys.readouterr().err
    assert "macOS hasn't allowed djlib to control apps yet" in err
    # The command stays on one line so it can be pasted.
    assert terminal.ACCESSIBILITY_SETTINGS in err.splitlines()[-1]
    # The message wraps under its indent instead of running back to column 0.
    assert all(indent >= 2 for indent in indents(err)[1:])


def test_ffmpeg_errors_suggest_installing_it(capsys, monkeypatch):
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    terminal.render_error(
        sized(100),
        {"code": "DEPENDENCY_REQUIRED", "message": "This format requires ffprobe and FFmpeg."},
    )
    assert "brew install ffmpeg" in capsys.readouterr().err


def test_long_fields_and_headlines_wrap_with_a_hanging_indent(capsys):
    term = sized(50)
    views.status_line(term, "ok", "Friday — Warm-up at The Extremely Long Venue Name", "8 of 12")
    views.fields(term, [("Source", "SoundCloud: " + "a very long title " * 6)])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) > 3
    assert lines[0].startswith("✓ Friday") and lines[1].startswith("  ")
    source = next(i for i, line in enumerate(lines) if "Source" in line)
    column = lines[source].index("SoundCloud")
    assert all(line.startswith(" " * column) for line in lines[source + 1 :])


def test_narrow_track_tables_drop_file_and_format_before_titles(capsys):
    tracks = [
        {
            "artist": "Velvet Static & The Extremely Long Collaborator Name",
            "title": "Night Bus (Extended Vocal Mix)",
            "path": "/music/House/Velvet Static - Night Bus (Extended Vocal Mix).aiff",
            "properties": {"duration_seconds": 412, "codec": "pcm_s16be", "sample_rate": 44100},
            "dj": {"bpm": 124, "key": "8A"},
        }
    ]
    out = render("library", {"tracks": tracks, "total": 1}, width=80, capsys=capsys)
    assert "File" not in out and "Night Bus (Extended Vocal" in out
    narrow = render("library", {"tracks": tracks, "total": 1}, width=64, capsys=capsys)
    assert "Format" not in narrow and "Night Bus (Extended Voc" in narrow


def test_tables_show_short_ids_and_keep_names(capsys):
    jobs = {
        "jobs": [
            {
                "job_id": JOB,
                "kind": "organize",
                "name": "Friday Warm-up at The Venue",
                "state": "completed",
                "outcome": "complete",
                "counts": {"succeeded": 8},
                "updated_at": "2026-10-06T10:00:00+00:00",
            }
        ],
        "total": 1,
    }
    out = render("jobs list", jobs, width=60, capsys=capsys)
    assert "Name" in out and "Friday Warm-up" in out
    assert "ee06f031" in out and JOB not in out
    rows = {
        "collections": [
            {"collection_id": COLLECTION, "name": "Friday", "track_count": 8, "created_at": ""}
        ],
        "total": 1,
    }
    out = render("collections", rows, width=50, capsys=capsys)
    assert "b3dbc172" in out and COLLECTION not in out and "Friday" in out
    assert terminal.short_id(REQUEST) == "83978f59" and terminal.short_id("plain") == "plain"


def test_warnings_print_under_the_headline(capsys):
    out = render(
        "set",
        {"name": "Friday", "songs": 2, "owned": 0, "missing": [], "request_id": REQUEST},
        warnings=["line 12 skipped (no “Artist - Title” separator)"],
        capsys=capsys,
    )
    lines = [line for line in out.splitlines() if line.strip()]
    assert "Friday" in lines[0] and "line 12 skipped" in lines[1]


def test_paged_screens_keep_a_single_next_block(capsys):
    out = render(
        "crate",
        {
            "collection_id": COLLECTION,
            "name": "Friday",
            "track_count": 30,
            "tracks": [{"artist": "A", "title": "One", "properties": {}}],
            "next_cursor": 1,
        },
        width=160,
        capsys=capsys,
    )
    assert out.count("Next\n") == 1
    assert f"more: djlib crate {COLLECTION} --after 1" in out
    request = {
        "request_id": REQUEST,
        "revision": 2,
        "name": "Friday",
        "total_items": 30,
        "counts": {"satisfied": 1},
        "items": [{"position": 1, "state": "satisfied", "input": {"artist": "A", "title": "B"}}],
        "next_offset": 1,
    }
    out = render("requests get", request, width=160, capsys=capsys)
    assert out.count("Next\n") == 1 and f"requests get {REQUEST} --after 1" in out


def test_request_lists_lead_with_building_the_crate(capsys):
    request = {
        "request_id": REQUEST,
        "revision": 2,
        "name": "Friday",
        "total_items": 2,
        "counts": {"satisfied": 1, "missing": 1},
        "unresolved_items": 1,
        "items": [
            {"position": 1, "state": "satisfied", "input": {"artist": "A", "title": "B"}},
            {"position": 2, "state": "missing", "input": {"artist": "C", "title": "D"}},
        ],
    }
    out = render("requests create", request, width=160, capsys=capsys)
    steps = out.split("Next\n", 1)[1]
    assert steps.index("Build the crate from the 1 song you own") < steps.index("report")
    assert f"djlib requests collect {SHORT_REQUEST}" in steps
    assert f"djlib requests report {SHORT_REQUEST}" in steps
    assert f"djlib requests refresh {SHORT_REQUEST}" in steps
    assert "--revision" not in out


def test_crate_job_card_is_short_and_points_at_rekordbox(capsys):
    job = {
        "job_id": JOB,
        "kind": "organize",
        "state": "completed",
        "outcome": "complete",
        "counts": {"succeeded": 8},
        "result": {"collection_id": COLLECTION, "name": "Friday", "selected_count": 8},
        "updated_at": "2026-10-06T10:00:00+00:00",
    }
    out = render("jobs get", job, width=160, capsys=capsys)
    assert out.startswith("✓ Crate built: 8 tracks")
    assert "Job" not in out and "Updated" not in out and "succeeded" not in out
    steps = [" ".join(line.split()[1:]) for line in out.split("Next\n", 1)[1].splitlines()]
    assert steps[0] == f"Put it in rekordbox djlib rekordbox push {SHORT_COLLECTION}"
    assert steps[1] == f"Or straight onto your USB djlib rekordbox usb {SHORT_COLLECTION}"
    assert (
        steps[2] == f"See its tracks djlib crate {SHORT_COLLECTION}"
    )  # `crate` is the command name


def failed_scan(failed: int = 12) -> dict:
    return {
        "job_id": JOB,
        "kind": "scan",
        "state": "completed",
        "outcome": "completed_with_gaps",
        "counts": {"succeeded": 400, "failed": failed},
        "result": {"discovered_files": 400 + failed, "skipped_files": {}},
        "updated_at": "2026-10-06T10:00:00+00:00",
    }


def test_scan_failures_are_grouped_by_reason(capsys, monkeypatch):
    error = {"code": "DEPENDENCY_REQUIRED", "message": "This format requires ffprobe and FFmpeg."}
    monkeypatch.setattr(
        views, "failed_items", lambda job_id: [{"result": {"error": error}}] * 3
    )  # a sample of the failures, all for one reason
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    out = render("jobs get", failed_scan(), width=160, capsys=capsys)
    assert out.startswith("! 400 tracks indexed, 12 failed")
    assert "12 files need FFmpeg" in out
    steps = out.split("Next\n", 1)[1]
    assert steps.index("brew install ffmpeg") < steps.index(f"djlib jobs control {JOB} retry")
    assert JOB in out  # something failed, so the job ID is shown


def test_scan_over_the_item_limit_suggests_a_subfolder(capsys):
    job = {
        **failed_scan(0),
        "state": "failed",
        "outcome": None,
        "counts": {},
        "result": {"error": {"code": "ITEM_LIMIT", "message": "A scan is limited to 10,000."}},
    }
    out = render("jobs get", job, width=160, capsys=capsys)
    assert "djlib scan PATH" in out
    assert "jobs items" not in out and "retry" not in out


def test_terminal_scan_names_why_files_failed(local_http, audio_factory):
    (audio_factory("good.wav").parent / "broken.wav").write_bytes(b"not audio at all")
    scanned = invoke("scan", pretty=True, workspace=local_http.root)
    assert scanned.exit_code == 4, scanned.output
    assert "1 failed" in scanned.stdout
    failed = scanned.stdout.split("Failed", 1)[1].splitlines()[0]
    assert "1 file" in failed and len(failed.strip()) > len("1 file")


def push_result(matched: int) -> dict:
    return {
        "crates": [
            {
                "collection_id": COLLECTION,
                "playlist": "Friday [/] [bold]Warm-up",
                "status": "already_in_rekordbox",
                "expected": 21,
                "matched": matched,
                "analyzed": 0,
            }
        ],
        "rekordbox_ui_seconds": 7.4,
    }


def test_push_warns_about_missing_tracks_and_points_at_the_usb(capsys):
    out = render("rekordbox push", push_result(21), width=160, capsys=capsys)
    assert "rekordbox needed the screen for 7 s" in out
    assert "✓ Friday [/] [bold]Warm-up" in out
    assert f"Put it on your USB  djlib rekordbox usb {SHORT_COLLECTION}" in out
    short = render("rekordbox push", push_result(20), width=160, capsys=capsys)
    assert short.startswith("! In rekordbox: 1 playlist, 1 incomplete")
    assert "! Friday [/] [bold]Warm-up" in short and "20 of 21 tracks" in short
    assert "1 track from the crate is not in this playlist" in short


USB = {
    "collection_id": COLLECTION,
    "playlist": "Friday",
    "device": "/Volumes/RICARDO_AM",
    "expected": 8,
    "found": 7,
    "in_order": True,
    "playlist_on_device": True,
    "missing": ["Âme - Rej (Original Mix)"],
    "library_updated": True,
}


def test_a_short_usb_export_is_a_failure(capsys):
    out = render("rekordbox usb", USB, width=160, capsys=capsys)
    assert out.startswith(
        "✗ USB check failed: 1 of 8 missing on RICARDO_AM — don't take this stick yet"
    )
    assert "Âme - Rej (Original Mix)" in out
    assert f"djlib rekordbox usb {SHORT_COLLECTION}" in out
    shuffled = render(
        "rekordbox usb", {**USB, "found": 8, "missing": [], "in_order": False}, capsys=capsys
    )
    assert "USB check failed: tracks out of order on RICARDO_AM" in shuffled
    assert "out of order" in shuffled.split("Tracks", 1)[1]


def test_set_with_a_failed_usb_check_leads_with_it(capsys):
    result = {
        "name": "Friday",
        "songs": 9,
        "owned": 8,
        "request_id": REQUEST,
        "collection_id": COLLECTION,
        "playlist": "Friday",
        "rekordbox": {"status": "imported"},
        "missing": [],
        "usb": USB,
    }
    out = render("set", result, width=160, capsys=capsys)
    assert out.startswith("✗ USB check failed: 1 of 8 missing on RICARDO_AM")
    assert "Friday  8 of 9 songs owned" in out and "Âme - Rej (Original Mix)" in out
    assert f"Export it to the stick again  djlib rekordbox usb {SHORT_COLLECTION}" in out


def test_set_and_push_views_print_markup_like_titles_literally(capsys):
    title = "Weird [/] [bold]name[/bold] \\[x]"
    result = {
        "name": title,
        "songs": 2,
        "owned": 1,
        "request_id": REQUEST,
        "collection_id": COLLECTION,
        "playlist": title,
        "rekordbox": {"status": "imported"},
        "missing": [{"position": 2, "label": title, "state": "missing", "you_own": [title]}],
        "fetched": {"needs_your_pick": [{"label": title, "options": []}]},
        "id_hints": [{"position": 2, "timestamp": "1:00", "hints": [{"label": title}]}],
    }
    out = render("set", result, width=200, capsys=capsys)
    assert out.count(title) >= 5
    push = push_result(21)
    push["crates"][0]["playlist"] = title
    assert title in render("rekordbox push", push, width=200, capsys=capsys)
    listed = {"collections": [{"collection_id": COLLECTION, "name": title, "track_count": 1}]}
    assert title in render("collections", listed, width=200, capsys=capsys)
    assert title in render("generic", {"name": title}, width=200, capsys=capsys)


def status_result(**changes) -> dict:
    result = {
        "workspace": "/music/djlib",
        "tracks": 20,
        "with_bpm": 20,
        "with_key": 20,
        "recent_collections": [],
        "recent_requests": [],
        "rekordbox_checked": True,
        "service_url": "http://127.0.0.1:1",
    }
    return {**result, **changes}


def test_status_ends_with_a_next_step_and_hides_the_service(capsys):
    crate = {
        "collection_id": COLLECTION,
        "name": "Friday",
        "tracks": 8,
        "in_rekordbox": True,
        "usb": {"device": "STICK", "found": 8, "expected": 8, "in_order": True},
    }
    out = render("status", status_result(recent_collections=[crate]), width=160, capsys=capsys)
    assert "Service" not in out and "running" not in out
    assert "djlib set TRACKLIST.txt" in out.split("Next\n", 1)[1]
    unread = render(
        "status",
        status_result(
            rekordbox_checked=False, recent_collections=[{**crate, "in_rekordbox": None}]
        ),
        capsys=capsys,
    )
    assert "rekordbox: not readable right now" in unread


def test_status_rebuilds_a_crate_its_list_has_outgrown(capsys):
    crate = {"collection_id": COLLECTION, "name": "Saturday", "tracks": 18, "in_rekordbox": False}
    request = {
        "request_id": REQUEST,
        "revision": 3,
        "name": "Saturday",
        "songs": 24,
        "owned": 21,
        "missing": 3,
        "unresolved": 3,
    }
    out = render(
        "status",
        status_result(recent_collections=[crate], recent_requests=[request]),
        width=160,
        capsys=capsys,
    )
    steps = out.split("Next\n", 1)[1]
    assert "Rebuild “Saturday” with the 21 songs you own" in steps
    assert f"djlib requests collect {SHORT_REQUEST}" in steps
    assert "rekordbox push" not in steps  # the outdated crate is not pushed
    assert "21 of 24 owned" in out


def test_small_screens_say_only_what_is_true(capsys):
    demo = render("demo", {"ingestion": {"result": {"collection_id": "c"}}}, capsys=capsys)
    assert "service stop" not in demo
    page = render("ui", {"url": "http://127.0.0.1:1/ui/"}, capsys=capsys)
    assert "opening" not in page and "opened" not in page
    assert "opened in your browser" in render(
        "ui", {"url": "http://127.0.0.1:1/ui/", "opened": True}, capsys=capsys
    )


@pytest.mark.skipif(os.name == "nt", reason="tilde paths are POSIX shell syntax")
def test_copyable_commands_keep_tilde_paths_unquoted(capsys):
    folder = str(Path.home() / "Music" / "djlib-test")
    out = render("roots add", {"allowed_roots": [folder], "added": [folder]}, capsys=capsys)
    assert "djlib scan ~/Music/djlib-test" in out
    session = str(Path.home() / "djlib-session")
    out = render(
        "setup-agent",
        {
            "session_directory": session,
            "launch_claude": ["/usr/bin/python3", session + "/launch.py", "claude"],
        },
        width=40,
        capsys=capsys,
    )
    lines = [line.strip() for line in out.splitlines()]
    assert "/usr/bin/python3 ~/djlib-session/launch.py claude" in lines  # one unbroken line
    assert "'~" not in out


def test_crate_commands_render_with_the_collection_views(capsys):
    assert views.VIEWS["crates"] is views.VIEWS["collections"]
    assert views.VIEWS["crate"] is views.VIEWS["collection"]
    assert "No crates yet" in render("crates", {"collections": []}, capsys=capsys)


def test_doctor_before_init_shows_the_first_step_and_each_fix(capsys):
    result = {
        "workspace": "/somewhere/djlib",
        "workspace_initialized": False,
        "required_checks_passed": False,
        "workspace_id": None,
        "coordinator_url": None,
        "ffmpeg": None,
        "ffprobe": None,
        "fixes": {
            "workspace": "djlib init --allow-root ~/Music",
            "ffmpeg": "brew install ffmpeg",
            "ffprobe": "brew install ffmpeg",
            "javascript_runtime": "brew install deno",
        },
        "javascript_runtimes": {"youtube_runtime_ready": False},
    }
    out = render("doctor", result, width=100, capsys=capsys)
    lines = out.splitlines()
    workspace = next(i for i, line in enumerate(lines) if "Workspace" in line)
    assert "○ Workspace" in lines[workspace] and "not set up yet" in lines[workspace]
    assert lines[workspace + 1].strip() == "→ djlib init --allow-root ~/Music"
    ffmpeg = next(i for i, line in enumerate(lines) if "FFmpeg " in line)
    assert "✗ FFmpeg" in lines[ffmpeg] and lines[ffmpeg + 1].strip() == "→ brew install ffmpeg"
    assert "→ brew install deno" in out
    # The fix starts under the detail column.
    assert lines[ffmpeg + 1].index("→") == lines[ffmpeg].index("missing")
    assert "Fix the items marked ✗" in out
    ready = render(
        "doctor",
        {**result, "workspace_initialized": True, "ffmpeg": "/usr/bin/ffmpeg"},
        width=100,
        capsys=capsys,
    )
    assert "init --allow-root" not in ready and "brew install ffmpeg" in ready  # ffprobe


def test_set_notes_fit_in_two_short_lines():
    lines = views.set_notes(downloaded=True, hints=True, usb=True)
    assert len(lines) == 2 and all(len(line) <= 78 for line in lines)
    assert views.set_notes(downloaded=False, hints=False, usb=False) == [
        "Exact artist/title/version matches only."
    ]


def test_sixteen_color_terminals_get_a_yellow_accent():
    console = terminal.fit_colors(
        Console(theme=terminal.THEME, color_system="standard", force_terminal=True)
    )
    assert console.get_style("accent").color.name == "yellow"
    rich = terminal.fit_colors(
        Console(theme=terminal.THEME, color_system="truecolor", force_terminal=True)
    )
    assert rich.get_style("accent").color.name == "#f59f00"


def test_good_news_colors_only_its_glyph():
    text = views.marked(terminal.Terminal(json=False), "ok", "in rekordbox")
    assert [span.style for span in text.spans] == ["ok"]
    assert text.spans[0].end == 1
