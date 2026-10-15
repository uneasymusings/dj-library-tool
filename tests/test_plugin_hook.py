"""The plugin's SessionStart hook (hooks/session-start.sh): silent when djlib is ready, and one
fix for the assistant when the engine is missing, off Claude Code's PATH or another release."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "session-start.sh"
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("sh") is None, reason="the hook is a POSIX sh script"
)


def program(folder: Path, name: str, version: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "djlib {version}"\n' if version else "#!/bin/sh\n")
    path.chmod(0o755)
    return path


def hook(home: Path, *path: Path) -> str:
    """The hook's message, run as Claude Code runs it, with only these folders on PATH."""
    system = "/usr/bin:/bin"  # sed, head, dirname
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([*map(str, path), system]),
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
    }
    done = subprocess.run(
        ["sh", str(HOOK)], env=env, capture_output=True, text=True, timeout=10, check=True
    )
    assert done.stderr == ""
    return done.stdout


def test_the_hook_is_silent_when_djlib_is_on_path_and_matches(tmp_path):
    bin_folder = tmp_path / "home" / ".local" / "bin"
    program(bin_folder, "djlib", PLUGIN["version"])
    assert hook(tmp_path / "home", bin_folder) == ""


def test_djlib_off_claude_codes_path_gets_one_line_with_the_fix(tmp_path):
    home = tmp_path / "home"
    bin_folder = home / ".local" / "bin"
    program(bin_folder, "djlib", PLUGIN["version"])
    out = hook(home)
    assert out.splitlines() == [
        "djlib is in ~/.local/bin, which isn't on Claude Code's PATH, so the djlib MCP tools "
        'can\'t start. Offer to add `export PATH="$HOME/.local/bin:$PATH"` to their shell '
        "profile, then restart Claude Code from a new terminal window."
    ]
    # uv beside it (where its installer puts it) adds the folder to the shell's PATH.
    program(bin_folder, "uv")
    assert "Offer to run `~/.local/bin/uv tool update-shell`, then" in hook(home)
    program(tmp_path / "uv", "uv")
    assert "Offer to run `uv tool update-shell`, then" in hook(home, tmp_path / "uv")


def test_djlib_off_path_and_from_another_release_upgrades_by_its_full_path(tmp_path):
    home = tmp_path / "home"
    program(home / ".local" / "bin", "djlib", "0.1.0a1")
    lines = hook(home).splitlines()
    assert len(lines) == 3 and "isn't on Claude Code's PATH" in lines[0]
    assert f"The djlib engine (0.1.0a1) and the djlib plugin ({PLUGIN['version']})" in lines[1]
    assert "offer to run `~/.local/bin/djlib upgrade` (it updates both)" in lines[2]


def test_another_release_on_path_is_upgraded(tmp_path):
    program(tmp_path / "bin", "djlib", "0.1.0a1")
    out = hook(tmp_path / "home", tmp_path / "bin")
    assert "are from different releases" in out and "`djlib upgrade`" in out
    assert "PATH" not in out


def test_without_djlib_the_hook_offers_the_installer(tmp_path):
    out = hook(tmp_path / "home")
    assert "the djlib engine isn't" in out
    assert "install.sh | sh" in out and "PATH" not in out
