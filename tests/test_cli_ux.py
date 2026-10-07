"""What a DJ meets first: the command map, short handles, doctor, defaults and messages."""

import json
import shutil
import sys

import pytest
from pydantic import BaseModel, ValidationError, field_validator
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.contracts import ControlRequest
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestResolution
from djlib.interfaces import cli, handles, upgrade_cli
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.validation import validation_message
from tests.test_delivery_transports import assert_envelope
from tests.test_headline_workflow import build

PANELS = {
    "Start here": ["init", "scan", "set", "status", "doctor"],
    "Your library": ["library", "crates", "crate", "requests", "ui"],
    "rekordbox and USB": ["rekordbox"],
    "Settings": ["use", "roots", "upgrade"],
    "Assistants": ["setup-agent", "mcp"],
    "More": ["jobs", "service", "reviews"],
}
HIDDEN = [
    *("plan", "start", "export", "import-rekordbox", "usb-preflight", "delivery", "organize"),
    *("reconcile", "download", "source-inspect", "schemas", "capabilities", "version"),
    *("collections", "collection", "demo", "completion"),
]


def run(*arguments, workspace=None, pretty=False, columns=120):
    # CI runners (GITHUB_ACTIONS, FORCE_COLOR) make Rich colour help text; tests read plain text.
    env = {
        "DJLIB_OUTPUT": "pretty" if pretty else None,
        "COLUMNS": str(columns),
        "NO_COLOR": "1",
        "FORCE_COLOR": None,
        "GITHUB_ACTIONS": None,
        "TTY_COMPATIBLE": None,
        "TTY_INTERACTIVE": None,
    }
    prefix = ["--workspace", str(workspace)] if workspace is not None else []
    return CliRunner(env=env).invoke(cli_app, [*prefix, *map(str, arguments)])


def visible_panels(*path: str) -> dict[str, list[str]]:
    """Help panels and their commands, in help order, read from the CLI itself.

    Independent of how a platform's console draws the boxes.
    """
    import click
    import typer.main

    group = typer.main.get_command(cli_app)
    context = click.Context(group)
    for name in path:
        group = group.get_command(context, name)
        context = click.Context(group, parent=context)
    found: dict[str, list[str]] = {}
    for name in group.list_commands(context):
        command = group.get_command(context, name)
        if not command.hidden:
            panel = getattr(command, "rich_help_panel", None) or "Commands"
            found.setdefault(panel, []).append(name)
    return found


# -- the command map --------------------------------------------------------------------------


def test_help_shows_only_what_a_dj_needs_in_journey_panels():
    reply = run("--help", columns=80)
    assert reply.exit_code == 0
    found = visible_panels()
    assert found == PANELS and list(found) == list(PANELS)
    listed = {name for names in found.values() for name in names}
    assert listed.isdisjoint(HIDDEN)
    assert "--install-completion" not in reply.output
    assert "--version" in reply.output
    epilog = " ".join(reply.output.split())
    assert "New here? djlib init --allow-root ~/Music → djlib scan → djlib set tracklist.txt" in (
        epilog
    )
    assert "Advanced commands (delivery, organize, plan, export…) still work" in epilog
    assert "docs/ADVANCED.md" in epilog
    assert max(len(line) for line in reply.output.splitlines()) <= 80


@pytest.mark.parametrize("command", HIDDEN)
def test_hidden_commands_still_work(command):
    reply = run(command, "--help")
    assert reply.exit_code == 0, reply.output
    assert "Usage:" in reply.output


def test_advanced_doc_lists_every_hidden_command():
    from pathlib import Path

    text = (Path(__file__).parents[1] / "docs" / "ADVANCED.md").read_text(encoding="utf-8")
    for command in HIDDEN:
        assert f"djlib {command}" in text, command


def test_version_flag_and_hidden_completion_command():
    reply = run("--version")
    assert reply.exit_code == 0 and reply.output.strip() == f"djlib {__version__}"
    script = run("completion", "zsh")
    assert script.exit_code == 0 and "_DJLIB_COMPLETE" in script.output


def test_requests_subcommands_follow_the_workflow_and_keys_are_hidden():
    found = visible_panels("requests")
    assert found["Commands"] == ["create", "get", "collect", "refresh", "report", "resolve", "list"]
    for command in (["scan"], ["export"], ["start"], ["delivery", "prepare"]):
        text = run(*command, "--help").output
        assert "--key" not in text, command
    assert "idempotency" not in run("--help").output.lower()


def test_help_text_wraps_cleanly_on_every_platform():
    """Summaries fit one line of the command list, and no paragraph is hard-wrapped.

    Hard line breaks inside help paragraphs leave orphan words wherever the console's width
    differs (Windows consoles use one column less); short single-line paragraphs don't.
    """
    import click
    import typer.main

    def visit(group, context, path=()):
        for name in group.list_commands(context):
            command = group.get_command(context, name)
            if command.hidden:
                continue
            text = (command.help or "").strip()
            paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
            summary = paragraphs[0] if paragraphs else ""
            assert len(summary) <= 60, (*path, name, summary)
            assert all("\n" not in part for part in paragraphs), (*path, name)
            for parameter in command.params:
                if not getattr(parameter, "hidden", False) and parameter.help:
                    assert "\n" not in parameter.help.strip(), (*path, name, parameter.name)
            if isinstance(command, click.Group):
                visit(command, click.Context(command, parent=context), (*path, name))

    group = typer.main.get_command(cli_app)
    visit(group, click.Context(group))


def test_json_and_workspace_options_work_after_the_subcommand(tmp_path, monkeypatch, capsys):
    hoisted = cli.hoisted
    assert hoisted(["library", "-w", "lib", "--json"]) == ["-w", "lib", "--json", "library"]
    assert hoisted(["--json", "version", "--json"]) == ["--json", "version"]
    assert hoisted(["scan", "--workspace=lib", "music"]) == ["--workspace=lib", "scan", "music"]
    assert hoisted(["-w", "lib", "scan"]) == ["-w", "lib", "scan"]
    assert hoisted(["set", "x", "--", "-w", "y"]) == ["set", "x", "--", "-w", "y"]
    monkeypatch.setenv("DJLIB_OUTPUT", "pretty")
    missing = tmp_path / "elsewhere"
    monkeypatch.setattr(sys, "argv", ["djlib", "library", "--workspace", str(missing), "--json"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "WORKSPACE_REQUIRED" and str(missing.resolve()) in error["message"]


# -- crates and short handles -----------------------------------------------------------------


class FakeLists:
    """The saved-list endpoints, newest first, paged two at a time."""

    def __init__(self, kind, rows):
        self.kind, self.rows, self.calls = kind, rows, []

    def request(self, method, path, *, params=None, data=None):
        self.calls.append(params)
        terms = (params or {}).get("query", "").casefold().split()
        field = f"{self.kind}_id"
        hits = [
            row
            for row in self.rows
            if all(term in row["name"].casefold() or term in row[field] for term in terms)
        ]
        start = int(params.get("after") or 0)
        limit = min(int(params["limit"]), 2)
        page = hits[start : start + limit]
        more = start + limit < len(hits)
        key = "collections" if self.kind == "collection" else "requests"
        return {"result": {key: page, "next_cursor": str(start + limit) if more else None}}


def crates(*named):
    return FakeLists(
        "collection",
        [{"collection_id": f"collection_{hexa:0<32}", "name": name} for hexa, name in named],
    )


def test_handles_accept_ids_last_names_and_prefixes():
    lists = crates(
        ("bbb222", "Warm-up"),
        ("eee555", "Friday Early"),
        ("bbb333", "Friday Late"),
        ("aaa111", "Friday"),
    )

    def full(hexa):
        return f"collection_{hexa:0<32}"

    assert handles.resolve(lists, "collection", full("c" * 32)) == full("c" * 32)
    assert lists.calls == []  # a full ID needs no lookup
    assert handles.resolve(lists, "collection", "last") == full("bbb222")
    # "friday" matches three crates; the exact name is on the second page of results.
    assert handles.resolve(lists, "collection", "friday") == full("aaa111")
    assert lists.calls[-1]["after"] == "2"
    assert handles.resolve(lists, "collection", "WARM-UP") == full("bbb222")
    assert handles.resolve(lists, "collection", " Friday late ") == full("bbb333")
    assert handles.resolve(lists, "collection", "bbb333") == full("bbb333")
    assert handles.resolve(lists, "collection", "collection_BBB222") == full("bbb222")


def test_handles_report_unknown_and_ambiguous_values():
    lists = crates(("abc123", "Friday"), ("abc124", "Friday"), ("ddd444", "Sunday"))
    with pytest.raises(AppError) as error:
        handles.resolve(lists, "collection", "Friday")
    assert error.value.code == "AMBIGUOUS"
    assert "collection_abc123" in error.value.message and "collection_abc124" in error.value.message
    with pytest.raises(AppError) as error:
        handles.resolve(lists, "collection", "abc12")
    assert error.value.code == "NOT_FOUND"  # five characters is too short to be a prefix
    with pytest.raises(AppError) as error:
        handles.resolve(lists, "collection", "Saturday")
    assert error.value.code == "NOT_FOUND" and "djlib crates" in error.value.message
    requests = FakeLists("request", [])
    with pytest.raises(AppError) as error:
        handles.resolve(requests, "request", "last")
    assert error.value.code == "NOT_FOUND"
    with pytest.raises(AppError) as error:
        handles.resolve(requests, "request", "Tonight")
    assert "djlib requests list" in error.value.message


def test_crates_and_crate_render_like_collections(application, library_http, audio_factory):
    built = build(library_http, [audio_factory("crate.wav", frequency=510)], [("Lumen", "Halo")])
    collection_id = built["result"]["collection_id"]
    root = application.workspace.root
    listed = json.loads(run("crates", workspace=root).stdout)["result"]
    legacy = json.loads(run("collections", workspace=root).stdout)["result"]
    assert listed["collections"] == legacy["collections"]
    for handle in ("last", "owned", collection_id[len("collection_") :][:6], collection_id):
        reply = run("crate", handle, workspace=root)
        assert reply.exit_code == 0, (handle, reply.output)
        assert json.loads(reply.stdout)["result"]["collection_id"] == collection_id
    assert json.loads(run("collection", "last", workspace=root).stdout)["ok"] is True
    pretty_crates = run("crates", pretty=True, workspace=root).stdout
    pretty_legacy = run("collections", pretty=True, workspace=root).stdout
    assert "Owned" in pretty_crates and "Tracks" in pretty_crates
    assert pretty_crates == pretty_legacy
    pretty_crate = run("crate", "last", pretty=True, workspace=root).stdout
    assert "Lumen" in pretty_crate and "Put it in rekordbox" in pretty_crate
    missing = run("crate", "Nothing like it", workspace=root)
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)["error"]["code"] == "NOT_FOUND"


def test_request_commands_take_handles_and_default_to_the_current_revision(
    application, library_http, tmp_path
):
    ledger = assert_envelope(
        library_http.post(
            "/requests",
            json={
                "name": "Tonight",
                "idempotency_key": "tonight-handles",
                "items": [{"artist": "Nobody", "title": "Nothing"}],
            },
        ).json()
    )
    root = application.workspace.root

    def cli_result(*arguments):
        reply = run(*arguments, workspace=root)
        assert reply.exit_code == 0, reply.output
        return json.loads(reply.stdout)["result"]

    assert cli_result("requests", "get", "tonight")["request_id"] == ledger["request_id"]
    refreshed = cli_result("requests", "refresh", "last")
    # Nothing changed since the list was created, so the revision is kept.
    assert refreshed["revision"] == ledger["revision"]
    prefix = ledger["request_id"].removeprefix("request_")[:8]
    assert cli_result("requests", "report", prefix)["report_path"]
    resolution = tmp_path / "resolution.json"
    resolution.write_text(
        json.dumps({"action": "select_source", "source_url": "https://soundcloud.com/a/b"}),
        encoding="utf-8",
    )
    item_id = ledger["items"][0]["item_id"]
    resolved = cli_result("requests", "resolve", "Tonight", item_id, "--file", resolution)
    assert resolved["revision"] > refreshed["revision"]
    stale = run(
        "requests",
        "resolve",
        "Tonight",
        item_id,
        "--file",
        resolution,
        "--revision",
        1,
        workspace=root,
    )
    assert stale.exit_code == 2  # an explicit revision still guards against stale edits
    assert json.loads(stale.stdout)["error"]["code"] == "REQUEST_STALE"


# -- doctor -----------------------------------------------------------------------------------


@pytest.fixture
def no_rekordbox(monkeypatch):
    """Fail loudly if anything reaches for rekordbox off macOS."""
    from djlib.native import rekordbox_mac
    from djlib.sources import runtimes

    def touched(*_):
        raise AssertionError("doctor touched rekordbox off macOS")

    monkeypatch.setattr(rekordbox_mac, "installed", touched)
    monkeypatch.setattr(rekordbox_mac, "automation_allowed", touched)
    monkeypatch.setattr(runtimes, "javascript_runtimes", lambda: {"youtube_runtime_ready": True})


def test_doctor_works_before_init_and_says_how_to_start(tmp_path, monkeypatch, no_rekordbox):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    reply = run("doctor", workspace=tmp_path / "nothing-yet")
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    result = envelope["result"]
    assert result["workspace_initialized"] is False and result["workspace_id"] is None
    assert result["coordinator_url"] is None and result["rekordbox"] is None
    assert result["required_checks_passed"] is True
    assert result["fixes"] == {"workspace": "djlib init --allow-root ~/Music"}
    assert envelope["warnings"] == []  # the view renders the row; JSON readers get the fields
    assert envelope["result"]["fixes"]["workspace"] == "djlib init --allow-root ~/Music"
    pretty = run("doctor", pretty=True, workspace=tmp_path / "nothing-yet")
    # The doctor view shows a "not set up yet" row with the init command under it.
    assert "not set up yet" in pretty.stdout and "djlib init" in pretty.stdout
    assert not (tmp_path / "nothing-yet").exists()  # doctor changes nothing


@pytest.mark.parametrize(
    ("platform", "fix"),
    [
        ("darwin", "brew install ffmpeg"),
        ("linux", "sudo apt install ffmpeg"),
        ("win32", "winget install ffmpeg"),
    ],
)
def test_doctor_fails_without_ffmpeg_and_names_the_fix(
    application, monkeypatch, no_rekordbox, platform, fix
):
    from djlib.interfaces.client import LocalClient

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(LocalClient, "discover", lambda self: None)
    if platform == "darwin":
        from djlib.native import rekordbox_mac

        monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
        monkeypatch.setattr(rekordbox_mac, "automation_allowed", lambda: True)
    reply = run("doctor", workspace=application.workspace.root)
    assert reply.exit_code == 1
    envelope = json.loads(reply.stdout)
    result = envelope["result"]
    assert result["workspace_initialized"] is True and result["workspace_id"]
    assert result["required_checks_passed"] is False
    assert result["fixes"]["ffmpeg"] == result["fixes"]["ffprobe"] == fix
    assert envelope["result"]["fixes"]["ffmpeg"] == fix


@pytest.mark.parametrize(
    ("plugin", "fix", "hint"),
    [
        ("0.1.0a1", upgrade_cli.PLUGIN_UPDATE, f"older than djlib {__version__}"),
        (__version__, None, None),
        ("99.0.0", "djlib upgrade", f"newer than djlib {__version__}"),
    ],
)
def test_doctor_flags_a_claude_code_plugin_from_another_release(
    tmp_path, monkeypatch, no_rekordbox, plugin, fix, hint
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    cache = tmp_path / "claude" / "plugins/cache/dj-library-tool/djlib"
    (cache / plugin).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    reply = run("doctor", workspace=tmp_path / "nothing-yet")
    assert reply.exit_code == 0, reply.output  # the plugin is never a required check
    result = json.loads(reply.stdout)["result"]
    assert result["claude_plugin"] == plugin
    assert result["fixes"].get("claude_plugin") == fix
    pretty = run("doctor", pretty=True, workspace=tmp_path / "nothing-yet", columns=200).stdout
    row = next(line for line in pretty.splitlines() if "Claude Code plugin" in line)
    assert plugin in row
    if fix:
        assert "✗ Claude Code plugin" in row and hint in row and fix in pretty
    else:
        assert "✓ Claude Code plugin" in row


def test_doctor_has_no_plugin_row_without_the_plugin(tmp_path, monkeypatch, no_rekordbox):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert (
        json.loads(run("doctor", workspace=tmp_path / "w").stdout)["result"]["claude_plugin"]
        is None
    )
    assert "Claude Code plugin" not in run("doctor", pretty=True, workspace=tmp_path / "w").stdout


# -- messages ---------------------------------------------------------------------------------


def test_validation_lists_choices_limits_and_validator_reasons():
    with pytest.raises(ValidationError) as error:
        ControlRequest(action="stop")
    assert validation_message(error.value) == (
        "Invalid input: action: choose 'pause', 'resume', 'cancel' or 'retry'"
    )
    with pytest.raises(ValidationError) as error:
        RequestResolution.model_validate({"revision": 0, "action": "satisfy"})
    message = validation_message(error.value)
    assert "revision: below allowed minimum; use at least 1" in message
    with pytest.raises(ValidationError) as error:
        RequestResolution.model_validate({"revision": 1, "action": "satisfy"})
    assert validation_message(error.value) == (
        "Invalid input: input: Satisfaction requires a recording ID and explicit evidence notes."
    )


def test_validator_messages_that_repeat_the_input_stay_private():
    class Leaky(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def check(cls, value):
            raise ValueError(f"{value} is not allowed")

    with pytest.raises(ValidationError) as error:
        Leaky(name="PRIVATE_INPUT")
    message = validation_message(error.value)
    assert "PRIVATE_INPUT" not in message
    assert message == "Invalid input: name: value violates this field's constraints"


def test_missing_input_file_is_named(application, tmp_path):
    missing = tmp_path / "wanted.json"
    reply = run("requests", "create", "--file", missing, workspace=application.workspace.root)
    assert reply.exit_code == 2
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "INPUT_INVALID"
    assert error["message"] == f"Can't open {missing}: no such file."
    folder = run("plan", "--file", tmp_path, workspace=application.workspace.root)
    assert json.loads(folder.stdout)["error"]["message"].startswith(f"Can't open {tmp_path}:")
