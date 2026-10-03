"""Create a project-scoped skill and explicit host launchers without personal config writes."""

import os
import shutil
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from djlib.domain.errors import AppError
from djlib.workspace import Workspace, atomic_json

# Kept as a plain Python launcher so paths and quoting also work on Windows.
LAUNCHER = '''"""Launch an installed AI host with this session's djlib MCP tools."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
config = json.loads((root / "session.json").read_text(encoding="utf-8"))
if len(sys.argv) < 2 or sys.argv[1] not in {"codex", "claude"}:
    raise SystemExit("Usage: python launch.py codex|claude [host arguments]")
host = sys.argv[1]
executable = shutil.which(host)
if not executable:
    raise SystemExit(f"Install and sign into {host} first; it is not on PATH.")
server = config["server"]
if host == "codex":
    # --ignore-user-config is currently an exec option, not an interactive option.
    extra = sys.argv[2:]
    command = [executable]
    if extra and extra[0] == "exec":
        command += ["exec", "--ignore-user-config"]
        extra = extra[1:]
    command += ["-C", str(root),
        "-c", "mcp_servers.djlib.command=" + json.dumps(server["command"]),
        "-c", "mcp_servers.djlib.args=" + json.dumps(server["args"]),
        "-c", "mcp_servers.djlib.startup_timeout_sec=30",
        "-c", "mcp_servers.djlib.tool_timeout_sec=120"]
else:
    extra = sys.argv[2:]
    command = [executable, "--strict-mcp-config", "--mcp-config", str(root / "mcp.json")]
raise SystemExit(subprocess.call(command + extra, cwd=root))
'''


def create_agent_session(workspace: Workspace, output: Path) -> dict:
    """Publish a complete session directory atomically; refuse existing contents."""
    workspace.config()
    output = output.expanduser().resolve()
    if output.exists():
        raise AppError(
            "SESSION_EXISTS", "Choose a new output directory; existing data is preserved."
        )
    if output.is_relative_to(workspace.root):
        raise AppError(
            "SESSION_PATH_INVALID", "Keep the agent session outside the music workspace."
        )
    # Wheels contain the same canonical skill as the source tree (Hatch force-include).
    packaged = files("djlib").joinpath("resources", "skills", "dj-library")
    source = Path(str(packaged))
    if not source.is_dir():
        source = Path(__file__).resolve().parents[3] / "skills" / "dj-library"
    if not (source / "SKILL.md").is_file():
        raise AppError("SKILL_MISSING", "Reinstall djlib; the portable workflow skill is missing.")
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".djlib-session-", dir=output.parent))
    try:
        server = {
            "command": sys.executable,
            "args": [
                "-m",
                "djlib.interfaces.cli",
                "--workspace",
                str(workspace.root),
                "mcp",
                "serve",
            ],
        }
        for host_dir in (".agents", ".claude"):
            shutil.copytree(source, staged / host_dir / "skills" / "dj-library")
        atomic_json(staged / "session.json", {"workspace": str(workspace.root), "server": server})
        atomic_json(staged / "mcp.json", {"mcpServers": {"djlib": {"type": "stdio", **server}}})
        (staged / "launch.py").write_text(LAUNCHER, encoding="utf-8")
        (staged / "AGENTS.md").write_text(
            "# DJ library session\n\n"
            "Use the dj-library skill and djlib MCP tools. Read capabilities first.\n"
            f"The configured workspace is {workspace.root}.\n"
            "Keep unresolved identities and quality limits explicit. "
            "Accepted jobs survive host exit.\n"
            "Exports are app handoff artifacts; "
            "native DJ app analysis/export is a separate step.\n",
            encoding="utf-8",
        )
        (staged / "README.md").write_text(
            "# Fresh assistant session\n\n"
            "From this directory, use your installed Python to run:\n\n"
            "```sh\npython launch.py codex\npython launch.py claude\n```\n\n"
            "The launchers supply explicit local MCP configuration. "
            "Codex exec ignores personal config; interactive Codex retains personal host settings. "
            "Claude uses only this MCP config but retains other personal host settings. "
            "Existing host sign-in and execution permissions still apply. "
            "The project contains the portable skill for both hosts. "
            "No personal host configuration or credentials are copied or changed.\n\n"
            "Try: Read djlib capabilities, inspect the existing collections, prepare an export, "
            "and report the exact app import paths and remaining USB steps.\n",
            encoding="utf-8",
        )
        os.rename(staged, output)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {
        "session_directory": str(output),
        "workspace": str(workspace.root),
        "launch_codex": [sys.executable, str(output / "launch.py"), "codex"],
        "launch_claude": [sys.executable, str(output / "launch.py"), "claude"],
        "personal_config_changed": False,
    }
