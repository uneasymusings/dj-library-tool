"""Portable skill packaging, scoped setup, quoting, and host launcher contracts."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from djlib.application.agent_setup import create_agent_session
from djlib.domain.errors import AppError
from djlib.workspace import Workspace


def test_setup_contains_both_skills_and_absolute_mcp_command(tmp_path):
    workspace = Workspace(tmp_path / "library with spaces")
    workspace.initialize()
    output = tmp_path / "session with spaces"
    result = create_agent_session(workspace, output)
    config = json.loads((output / "mcp.json").read_text())
    server = config["mcpServers"]["djlib"]
    # Delivery observations can reconcile native tags for up to 600 seconds.
    assert server["timeout"] > 600_000
    assert server["command"] == sys.executable
    assert server["args"] == [
        "-m",
        "djlib.interfaces.cli",
        "--workspace",
        str(workspace.root),
        "mcp",
        "serve",
    ]
    for host_dir in (".agents", ".claude"):
        skill = output / host_dir / "skills" / "dj-library"
        assert (skill / "SKILL.md").is_file() and (skill / "references" / "cli.md").is_file()
    assert result["personal_config_changed"] is False
    compile((output / "launch.py").read_text(), "launch.py", "exec")
    # The guidance matches the current rekordbox/USB workflow, not the old export handoff.
    guidance = (output / "AGENTS.md").read_text()
    assert "djlib set FILE_OR_URL" in guidance and "background" in guidance
    assert "handoff" not in guidance and "--usb" in guidance
    assert "python3 launch.py claude" in (output / "README.md").read_text()


def test_setup_preserves_existing_session(tmp_path):
    workspace = Workspace(tmp_path / "library")
    workspace.initialize()
    output = tmp_path / "session"
    output.mkdir()
    (output / "important.txt").write_text("keep")
    with pytest.raises(AppError) as error:
        create_agent_session(workspace, output)
    assert error.value.code == "SESSION_EXISTS"
    assert (output / "important.txt").read_text() == "keep"


def test_setup_rejects_workspace_destination(tmp_path):
    workspace = Workspace(tmp_path / "library")
    workspace.initialize()
    with pytest.raises(AppError) as error:
        create_agent_session(workspace, workspace.root / "session")
    assert error.value.code == "SESSION_PATH_INVALID"


@pytest.mark.parametrize("host,extra", [("codex", []), ("codex", ["exec"]), ("claude", [])])
def test_launchers_supply_scoped_config_and_forward_arguments(tmp_path, monkeypatch, host, extra):
    import djlib.application.agent_setup as setup

    workspace = Workspace(tmp_path / "library")
    workspace.initialize()
    output = tmp_path / "session"
    setup.create_agent_session(workspace, output)
    recorded = {}

    def start(command, cwd, **kwargs):
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        recorded.update(startup=command, startup_cwd=cwd)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    def call(command, cwd, env):
        assert "startup" in recorded, "Coordinator must start before the assistant host"
        # The assistant's own djlib commands keep the JSON contract even inside a PTY.
        assert env["DJLIB_OUTPUT"] == "json"
        # rekordbox/USB steps run through the CLI, which must use the session's workspace.
        assert env["DJLIB_WORKSPACE"] == str(workspace.root)
        recorded.update(command=command, cwd=cwd)
        return 7

    monkeypatch.setattr(sys, "argv", [str(output / "launch.py"), host, *extra, "--help"])
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/installed/" + name)
    monkeypatch.setattr(subprocess, "call", call)
    monkeypatch.setattr(subprocess, "run", start)
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(setup.LAUNCHER, "launch.py", "exec"), {"__file__": str(output / "launch.py")})
    assert exit_info.value.code == 7
    command = recorded["command"]
    assert command[0] == "/installed/" + host and command[-1] == "--help"
    assert recorded["cwd"] == output
    assert recorded["startup"] == [
        sys.executable,
        "-m",
        "djlib.interfaces.cli",
        "--workspace",
        str(workspace.root),
        "service",
        "start",
    ]
    assert recorded["startup_cwd"] == output
    if host == "codex":
        assert ("--ignore-user-config" in command) == bool(extra)
        if extra:
            assert command[1:3] == ["exec", "--ignore-user-config"]
        assert any("mcp_servers.djlib.command=" in argument for argument in command)
        assert "mcp_servers.djlib.tool_timeout_sec=660" in command
    else:
        assert "--strict-mcp-config" in command and str(output / "mcp.json") in command


@pytest.mark.parametrize(
    "reply,exit_code",
    [
        (SimpleNamespace(returncode=2, stdout='{"ok":false}', stderr=""), 2),
        (SimpleNamespace(returncode=0, stdout='{"ok":false}', stderr=""), None),
        (SimpleNamespace(returncode=0, stdout="not json", stderr=""), None),
    ],
)
def test_launcher_does_not_launch_host_after_failed_startup(
    tmp_path, monkeypatch, reply, exit_code
):
    import djlib.application.agent_setup as setup

    workspace = Workspace(tmp_path / "library")
    workspace.initialize()
    output = tmp_path / "session"
    setup.create_agent_session(workspace, output)
    monkeypatch.setattr(sys, "argv", [str(output / "launch.py"), "codex"])
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/installed/" + name)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: reply)
    monkeypatch.setattr(subprocess, "call", lambda *_a, **_kw: pytest.fail("Host launched"))
    with pytest.raises(SystemExit) as raised:
        exec(compile(setup.LAUNCHER, "launch.py", "exec"), {"__file__": str(output / "launch.py")})
    if exit_code is not None:
        assert raised.value.code == exit_code
    else:
        assert "startup" in str(raised.value.code).lower()


def test_mcp_without_workspace_keeps_stdout_clean(tmp_path):
    reply = subprocess.run(
        [
            sys.executable,
            "-m",
            "djlib.interfaces.cli",
            "-w",
            str(tmp_path / "missing"),
            "mcp",
            "serve",
        ],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Whether the CLI refuses to start (exit 2, reason on stderr) or the server starts and
    # answers WORKSPACE_REQUIRED per call (exit 0 at end of input), stdout carries no text
    # that would corrupt the MCP transport.
    assert reply.stdout == ""
    assert reply.returncode == 0 or (reply.returncode == 2 and reply.stderr)
