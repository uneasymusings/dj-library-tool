"""Portable skill packaging, scoped setup, quoting, and host launcher contracts."""

import json
import subprocess
import sys

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

    def call(command, cwd):
        recorded.update(command=command, cwd=cwd)
        return 7

    monkeypatch.setattr(sys, "argv", [str(output / "launch.py"), host, *extra, "--help"])
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/installed/" + name)
    monkeypatch.setattr(subprocess, "call", call)
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(setup.LAUNCHER, "launch.py", "exec"), {"__file__": str(output / "launch.py")})
    assert exit_info.value.code == 7
    command = recorded["command"]
    assert command[0] == "/installed/" + host and command[-1] == "--help"
    assert recorded["cwd"] == output
    if host == "codex":
        assert ("--ignore-user-config" in command) == bool(extra)
        if extra:
            assert command[1:3] == ["exec", "--ignore-user-config"]
        assert any("mcp_servers.djlib.command=" in argument for argument in command)
        assert "mcp_servers.djlib.tool_timeout_sec=660" in command
    else:
        assert "--strict-mcp-config" in command and str(output / "mcp.json") in command


def test_mcp_startup_failure_keeps_stdout_clean(tmp_path):
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
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert reply.returncode == 2 and reply.stdout == "" and reply.stderr
