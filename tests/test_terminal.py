"""Terminal presentation: readable views for people, the JSON contract for everything else."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import cli, guided, terminal, views
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import create_app


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
    assert "Workspace required" in reply.stderr
    assert "init --allow-root PATH" in reply.stderr
    captured = invoke("library", workspace=tmp_path / "missing")
    assert json.loads(captured.stdout)["error"]["code"] == "WORKSPACE_REQUIRED"


def test_scripts_must_supply_submission_keys(tmp_path):
    reply = invoke("scan", str(tmp_path), workspace=tmp_path / "ws")
    assert reply.exit_code == 2
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "INPUT_INVALID" and "--key" in error["message"]


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
    assert "Music indexed" in scanned.stdout
    assert "2 succeeded" in scanned.stdout
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
    assert pretty.stdout.count("Music indexed") == 2
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
    term = terminal.Terminal(json=False)
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
