"""Run the complete shell installer with isolated, offline package-manager stand-ins."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
STAND_IN = r"""
import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["INSTALLER_TRACE"], "a") as trace:
    trace.write(json.dumps([name, *args]) + "\n")
state, runtime = Path(os.environ["INSTALLER_STATE"]), Path(os.environ["INSTALLER_RUNTIME"])
if name == "curl":
    print(json.dumps([{"assets":[{"browser_download_url":
        "https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a23/"
        "dj_library_tool-0.1.0a23-py3-none-any.whl"}]}]))
elif name == "djlib":
    if args == ["--json", "doctor"]:
        print(json.dumps({"ok":True,"result":{"javascript_runtimes":{
            "youtube_runtime_ready":runtime.exists()}}}))
    elif args == ["--json", "status"]:
        print(json.dumps({"ok":state.exists(),"error":None if state.exists() else
            {"code":"WORKSPACE_REQUIRED"}}))
        sys.exit(0 if state.exists() else 1)
    elif args and args[0] == "init":
        state.touch()
    elif args == ["doctor"]:
        print("Setup check complete")
elif name == "brew" and args == ["install", "deno"]:
    runtime.touch()
elif name == "brew" and args == ["install", "ffmpeg"]:
    for program in ("ffmpeg", "ffprobe"):
        executable = Path(os.environ["INSTALLER_BIN"]) / program
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the one-line installer uses POSIX sh")
@pytest.mark.parametrize(
    ("audio_ready", "runtime_ready", "homebrew"),
    [(False, False, True), (True, True, True), (True, False, False)],
)
def test_installer_finishes_with_one_next_step_and_only_installs_missing_helpers(
    tmp_path, audio_ready, runtime_ready, homebrew
):
    commands = tmp_path / "bin"
    commands.mkdir()
    for program in ("grep", "sed", "head", "uname"):
        path = shutil.which(program)
        assert path, f"Installer test needs {program}"
        (commands / program).symlink_to(path)
    programs = ["uv", "curl", "djlib"]
    if homebrew:
        programs.append("brew")
    if audio_ready:
        programs += ["ffmpeg", "ffprobe"]
    for program in programs:
        executable = commands / program
        executable.write_text(f"#!{sys.executable}\n{STAND_IN}", encoding="utf-8")
        executable.chmod(0o755)
    runtime = tmp_path / "runtime-ready"
    if runtime_ready:
        runtime.touch()
    music = tmp_path / "My Music"
    music.mkdir()
    trace = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": str(commands),
        "DJLIB_PLUGIN": "0",
        "DJLIB_MUSIC": str(music),
        "INSTALLER_BIN": str(commands),
        "INSTALLER_TRACE": str(trace),
        "INSTALLER_STATE": str(tmp_path / "indexed"),
        "INSTALLER_RUNTIME": str(runtime),
    }
    env.pop("DJLIB_VERSION", None)
    result = subprocess.run(
        ["/bin/sh", str(INSTALLER)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert ["djlib", "init", "--allow-root", str(music)] in calls
    assert ["djlib", "scan"] in calls
    assert (["brew", "install", "ffmpeg"] in calls) == (homebrew and not audio_ready)
    assert (["brew", "install", "deno"] in calls) == (homebrew and not runtime_ready)
    assert "Next, open your library:" in result.stdout and "djlib ui" in result.stdout
    assert "Import playlist or tracklist" in result.stdout
    assert not any(call[0] in {"claude", "codex"} for call in calls)
    if not runtime_ready and not homebrew:
        assert "For YouTube audio, install Deno:" in result.stdout
