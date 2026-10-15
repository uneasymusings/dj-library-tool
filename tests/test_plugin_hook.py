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

posix_sh = pytest.mark.skipif(
    os.name == "nt" or shutil.which("sh") is None, reason="the hook is a POSIX sh script"
)


def program(folder: Path, name: str, version: str = "", end: str = "\\n") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"#!/bin/sh\nprintf 'djlib {version}{end}'\n" if version else "#!/bin/sh\n")
    path.chmod(0o755)
    return path


def hook(home: Path, *path: Path) -> str:
    """The hook's message, run as Claude Code runs it, with only these folders on PATH (and the
    tools it uses, but not a uv that the system may have in /usr/bin)."""
    tools = home.parent / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("sed", "head", "dirname", "tr"):
        if not (tools / name).exists():
            (tools / name).symlink_to(shutil.which(name))
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([*map(str, path), str(tools)]),
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
    }
    done = subprocess.run(
        [shutil.which("sh"), str(HOOK)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert done.stderr == ""
    return done.stdout


def test_the_hook_has_unix_line_endings():
    # sh can't run it with Windows ones, which a clone on Windows gets without .gitattributes.
    assert b"\r" not in HOOK.read_bytes()


@posix_sh
def test_the_hook_is_silent_when_djlib_is_on_path_and_matches(tmp_path):
    bin_folder = tmp_path / "home" / ".local" / "bin"
    program(bin_folder, "djlib", PLUGIN["version"])
    assert hook(tmp_path / "home", bin_folder) == ""
    # Python on Windows ends the version line with CR LF: still the same release.
    program(bin_folder, "djlib", PLUGIN["version"], end="\\r\\n")
    assert hook(tmp_path / "home", bin_folder) == ""


@posix_sh
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


@posix_sh
def test_djlib_off_path_and_from_another_release_upgrades_by_its_full_path(tmp_path):
    home = tmp_path / "home"
    program(home / ".local" / "bin", "djlib", "0.1.0a1")
    lines = hook(home).splitlines()
    assert len(lines) == 3 and "isn't on Claude Code's PATH" in lines[0]
    assert f"The djlib engine (0.1.0a1) and the djlib plugin ({PLUGIN['version']})" in lines[1]
    assert "offer to run `~/.local/bin/djlib upgrade` (it updates both)" in lines[2]


@posix_sh
def test_another_release_on_path_is_upgraded(tmp_path):
    program(tmp_path / "bin", "djlib", "0.1.0a1")
    out = hook(tmp_path / "home", tmp_path / "bin")
    assert "are from different releases" in out and "`djlib upgrade`" in out
    assert "PATH" not in out


@posix_sh
def test_without_djlib_the_hook_offers_the_installer(tmp_path):
    out = hook(tmp_path / "home")
    assert "the djlib engine isn't" in out
    assert "install.sh | sh" in out and "PATH" not in out
