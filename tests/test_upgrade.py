import importlib.abc
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import upgrade_cli
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient


def test_versions_order_like_releases():
    key = upgrade_cli.version_key
    assert key("0.1.0a10") > key("0.1.0a9") > key("0.1.0a9.dev0") > key("0.1.0a3")
    assert key("0.1.0") > key("0.1.0rc1") > key("0.1.0b2") > key("0.1.0a11")
    assert key("0.2.0a1") > key("0.2.0.dev1") > key("0.1.9")
    assert key("0.1.0.post1") > key("0.1.0") and key("0.1.0a13") == key("0.1.0a13")


def test_latest_release_skips_drafts_and_finds_the_wheel(monkeypatch):
    releases = [
        {"draft": True, "assets": [{"name": "dj_library_tool-0.1.0a99-py3-none-any.whl"}]},
        {
            "draft": False,
            "tag_name": "v0.1.0a12",
            "html_url": "https://github.com/x/y/releases/tag/v0.1.0a12",
            "assets": [
                {"name": "SHA256SUMS", "browser_download_url": "https://e/s"},
                {
                    "name": "dj_library_tool-0.1.0a12-py3-none-any.whl",
                    "browser_download_url": "https://e/w.whl",
                },
            ],
        },
    ]

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return releases

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Reply())
    latest = upgrade_cli.latest_release()
    assert latest == {
        "version": "0.1.0a12",
        "tag": "v0.1.0a12",
        "url": "https://e/w.whl",
        "notes": "https://github.com/x/y/releases/tag/v0.1.0a12",
    }

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    with pytest.raises(AppError) as error:
        upgrade_cli.latest_release()
    assert error.value.code == "UPDATE_CHECK_FAILED" and error.value.retryable


def plugin_cache(*versions: str):
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins/cache/dj-library-tool/djlib"
    for version in versions:
        (folder / version).mkdir(parents=True)


def test_claude_plugin_version_is_the_newest_cached_folder():
    assert upgrade_cli.claude_plugin_version() is None
    plugin_cache("0.1.0a9", "0.1.0a12", "0.1.0a10", "not-a-version")
    assert upgrade_cli.claude_plugin_version() == "0.1.0a12"


def test_assistant_sessions_counts_running_mcp_servers(monkeypatch):
    listing = "\n".join(
        [
            "  101 /Users/dj/.local/share/uv/tools/dj-library-tool/bin/python "
            "/Users/dj/.local/bin/djlib mcp serve",
            "  102 /usr/bin/python3 -m djlib.interfaces.cli --workspace /w mcp serve",
            "  103 djlib --workspace /w scan",
            "  104 /bin/zsh -l",
            "  105 /usr/bin/grep mcp serve",
        ]
    )
    calls = []

    def ps(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=listing)

    monkeypatch.setattr(upgrade_cli, "WINDOWS", False)
    monkeypatch.setattr(upgrade_cli.subprocess, "run", ps)
    assert upgrade_cli.assistant_sessions() == 2
    assert calls == [["ps", "-axo", "pid=,args="]]

    def missing(command, **kwargs):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", missing)
    assert upgrade_cli.assistant_sessions() == 0
    monkeypatch.setattr(upgrade_cli, "WINDOWS", True)
    monkeypatch.setattr(upgrade_cli.subprocess, "run", lambda *a, **kw: pytest.fail("ps on nt"))
    assert upgrade_cli.assistant_sessions() == 0


class EnvironmentReplaced(importlib.abc.MetaPathFinder):
    """What uv leaves behind: anything not yet imported is gone (rich's Unicode tables were)."""

    def __init__(self):
        self.attempts: list[str] = []

    def find_spec(self, name, path=None, target=None):
        self.attempts.append(name)
        raise ModuleNotFoundError(f"No module named {name!r}")


def fake_upgrade(monkeypatch, *, version="99.0.0", running=False, finish_code=0):
    """`djlib upgrade` up to the install; returns the commands it runs and what it imported."""
    latest = {"version": version, "tag": f"v{version}", "url": "https://e/w.whl", "notes": "n"}
    monkeypatch.setattr(upgrade_cli, "latest_release", lambda: latest)
    monkeypatch.setattr(
        upgrade_cli.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(upgrade_cli, "installed_by_uv", lambda: True)
    url = "http://127.0.0.1:1" if running else None
    monkeypatch.setattr(LocalClient, "discover", lambda self: url)
    stopped = []
    monkeypatch.setattr(
        LocalClient, "request", lambda self, method, path, **kw: stopped.append(path) or {}
    )
    state = {"commands": [], "stopped": stopped, "replaced": EnvironmentReplaced()}

    def run(command, **kwargs):
        state["commands"].append(command)
        if command[1:3] == ["tool", "install"]:
            # uv swaps the tool environment underneath this process.
            state["modules"] = set(sys.modules)
            sys.meta_path.insert(0, state["replaced"])
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, finish_code)

    monkeypatch.setattr(upgrade_cli.subprocess, "run", run)
    return state


def upgrade(tmp_path, state, *arguments, pretty=False):
    env = {"DJLIB_OUTPUT": "pretty", "COLUMNS": "100", "NO_COLOR": "1"} if pretty else {}
    try:
        return CliRunner(env=env).invoke(cli_app, ["--workspace", str(tmp_path / "w"), *arguments])
    finally:
        if state["replaced"] in sys.meta_path:
            sys.meta_path.remove(state["replaced"])


@pytest.mark.parametrize("running", [False, True])
def test_upgrade_finishes_in_the_newly_installed_djlib(tmp_path, monkeypatch, running):
    """After the install nothing new is imported: the new djlib finishes and prints."""
    state = fake_upgrade(monkeypatch, running=running)
    reply = upgrade(tmp_path, state, "upgrade")
    assert reply.exit_code == 0, reply.output
    install, finish = state["commands"]
    # Song recognition (shazamio) has macOS and Windows wheels for Python 3.12, not 3.13.
    assert install == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--force",
        "--python",
        "3.12",
        "dj-library-tool[download,recognize] @ https://e/w.whl",
    ]
    assert finish == [
        sys.executable,
        "-m",
        "djlib.interfaces.cli",
        "--workspace",
        str((tmp_path / "w").resolve()),
        "--json",
        "upgrade",
        "--finished-from",
        __version__,
        "--notes",
        "n",
        *(["--restart-service"] if running else []),
    ]
    assert state["stopped"] == (["/shutdown"] if running else [])
    assert state["replaced"].attempts == [] and set(sys.modules) == state["modules"]
    assert reply.stdout == ""  # the new djlib wrote the reply


def test_a_terminal_upgrade_renders_nothing_after_the_install(tmp_path, monkeypatch):
    """The a15 repair upgrade crashed drawing its result: rich imported its Unicode tables
    after uv had deleted them. The terminal view now comes from the new djlib."""
    state = fake_upgrade(monkeypatch, running=True)
    reply = upgrade(tmp_path, state, "upgrade", pretty=True)
    assert reply.exit_code == 0, reply.output
    assert "--json" not in state["commands"][1] and "--restart-service" in state["commands"][1]
    # One line while uv works, said before it starts; nothing from this process after.
    assert state["replaced"].attempts == [] and reply.stdout == ""
    assert reply.stderr == "Installing djlib 99.0.0…\n"


def test_upgrade_exits_with_the_finishing_steps_code(tmp_path, monkeypatch):
    state = fake_upgrade(monkeypatch, finish_code=4)
    assert upgrade(tmp_path, state, "upgrade").exit_code == 4


def test_upgrade_says_it_installed_when_the_new_djlib_cannot_start(tmp_path, monkeypatch):
    state = fake_upgrade(monkeypatch)

    def run(command, **kwargs):
        if command[1:3] == ["tool", "install"]:
            sys.meta_path.insert(0, state["replaced"])
            return subprocess.CompletedProcess(command, 0, "", "")
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(upgrade_cli.subprocess, "run", run)
    reply = upgrade(tmp_path, state, "upgrade")
    assert reply.exit_code == 0 and state["replaced"].attempts == []
    result = json.loads(reply.stdout)["result"]
    assert result["updated"] is True and result["latest"] == "99.0.0"
    assert result["current"] == __version__


def finish(tmp_path, *arguments, pretty=False):
    env = {"DJLIB_OUTPUT": "pretty", "COLUMNS": "100", "NO_COLOR": "1"} if pretty else {}
    return CliRunner(env=env).invoke(
        cli_app,
        ["--workspace", str(tmp_path / "w"), "upgrade", "--finished-from", *arguments],
    )


@pytest.mark.parametrize("sessions", [0, 1, 2])
def test_the_new_djlib_says_which_sessions_and_plugin_still_run_the_old_one(
    tmp_path, monkeypatch, sessions
):
    # No `claude` on PATH: the plugin can't be updated here, so the user is told how.
    monkeypatch.setattr(upgrade_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: sessions)
    plugin_cache("0.1.0a12")
    reply = finish(tmp_path, "0.1.0a12", "--notes", "https://e/notes")
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    result = envelope["result"]
    assert result == {
        "current": "0.1.0a12",
        "latest": __version__,
        "notes": "https://e/notes",
        "update_available": True,
        "updated": True,
        "assistant_sessions": sessions,
    }
    warnings = envelope["warnings"]
    assert len(warnings) == 1 + bool(sessions)
    assert warnings[-1].startswith("The Claude Code plugin is still 0.1.0a12")
    assert upgrade_cli.PLUGIN_UPDATE in warnings[-1]
    if sessions:
        count = "1 assistant session is" if sessions == 1 else "2 assistant sessions are"
        assert warnings[0].startswith(f"{count} still running djlib 0.1.0a12")
        assert "/mcp → djlib → Reconnect" in warnings[0] and f"uses {__version__}" in warnings[0]
    pretty = finish(tmp_path, "0.1.0a12", "--notes", "https://e/notes", pretty=True).stdout
    assert pretty.startswith(f"✓ djlib {__version__} installed  from 0.1.0a12")
    assert "Release notes  https://e/notes" in pretty and pretty.rstrip().endswith("doctor")


def test_the_new_djlib_restarts_the_service_it_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: 0)
    requests = []
    monkeypatch.setattr(
        LocalClient, "request", lambda self, method, path, **kw: requests.append(path) or {}
    )
    reply = json.loads(finish(tmp_path, "0.1.0a12", "--restart-service").stdout)
    assert requests == ["/roots"] and reply["result"]["service_restarted"] is True

    def refused(self, method, path, **kwargs):
        raise AppError("SERVICE_START_FAILED", "no")

    monkeypatch.setattr(LocalClient, "request", refused)
    reply = json.loads(finish(tmp_path, "0.1.0a12", "--restart-service").stdout)
    assert reply["ok"] is True and reply["result"]["service_restarted"] is False
    assert "the next djlib command starts it" in reply["warnings"][0]
    assert "service_restarted" not in json.loads(finish(tmp_path, "0.1.0a12").stdout)["result"]


def test_upgrade_brings_the_claude_code_plugin_along(tmp_path, monkeypatch):
    """Engine and plugin from different releases broke the tools once; upgrade syncs both."""
    latest = {"version": "99.0.0", "tag": "v99.0.0", "url": "https://e/w.whl", "notes": "n"}
    monkeypatch.setattr(upgrade_cli, "latest_release", lambda: latest)
    monkeypatch.setattr(upgrade_cli.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(upgrade_cli, "installed_by_uv", lambda: True)
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: 0)
    monkeypatch.setattr(upgrade_cli, "recognition_installed", lambda: True)
    monkeypatch.setattr(LocalClient, "discover", lambda self: None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", run)
    plugin_cache("0.1.0a12")
    workspace = ["--workspace", str(tmp_path / "w")]
    # The newly installed djlib (here: this one) updates the plugin to its own release.
    envelope = json.loads(finish(tmp_path, "0.1.0a12").stdout)
    assert envelope["result"]["plugin_updated"] == {"from": "0.1.0a12", "to": __version__}
    assert commands == [
        ["/usr/local/bin/claude", "plugin", "marketplace", "update", "dj-library-tool"],
        ["/usr/local/bin/claude", "plugin", "update", "djlib@dj-library-tool"],
    ]
    assert "Restart Claude Code" in envelope["warnings"][-1]

    # Already on the newest engine: an outdated plugin is still brought up to date.
    commands.clear()
    latest["version"] = __version__
    current = json.loads(CliRunner().invoke(cli_app, [*workspace, "upgrade"]).stdout)
    assert current["result"]["updated"] is False
    assert current["result"]["plugin_updated"]["to"] == __version__ and len(commands) == 2

    # --check never changes anything.
    commands.clear()
    checked = json.loads(CliRunner().invoke(cli_app, [*workspace, "upgrade", "--check"]).stdout)
    assert "plugin_updated" not in checked["result"] and commands == []

    def refused(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "Marketplace not found")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", refused)
    failed = json.loads(CliRunner().invoke(cli_app, [*workspace, "upgrade"]).stdout)
    assert "plugin_updated" not in failed["result"]
    assert "(Marketplace not found)" in failed["warnings"][-1]
    assert upgrade_cli.PLUGIN_UPDATE in failed["warnings"][-1]


def test_the_installer_installs_song_recognition_on_python_3_12():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
    [install] = [line for line in script.splitlines() if line.startswith("uv tool install")]
    assert (
        "--python 3.12" in install and '"dj-library-tool[download,recognize] @ $wheel"' in install
    )


def test_upgrade_repairs_song_recognition_an_earlier_upgrade_left_out(tmp_path, monkeypatch):
    """0.1.0a14's upgrade installed newer releases without the recognize extra, on Python
    3.13; rerunning upgrade on the newest release installs it again with song recognition."""
    state = fake_upgrade(monkeypatch, version=__version__)
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: 0)
    monkeypatch.setattr(upgrade_cli, "recognition_installed", lambda: False)
    workspace = ["--workspace", str(tmp_path / "w")]
    checked = json.loads(CliRunner().invoke(cli_app, [*workspace, "upgrade", "--check"]).stdout)
    assert checked["result"]["recognition_installed"] is False and state["commands"] == []
    assert checked["result"]["repair_available"] is True
    env = {"DJLIB_OUTPUT": "pretty", "COLUMNS": "100", "NO_COLOR": "1"}
    told = CliRunner(env=env).invoke(cli_app, [*workspace, "upgrade", "--check"]).stdout
    assert "Song recognition  not installed" in told
    assert "→ Add song recognition" in told and told.rstrip().endswith(" upgrade")
    assert upgrade(tmp_path, state, "upgrade").exit_code == 0
    install, finish_step = state["commands"]
    assert install[-3:] == [
        "--python",
        "3.12",
        "dj-library-tool[download,recognize] @ https://e/w.whl",
    ]
    assert finish_step[-2:] == ["n", "--repaired"]
    reply = json.loads(finish(tmp_path, __version__, "--repaired").stdout)["result"]
    assert reply["updated"] is True and reply["repaired"] == "song recognition"
    assert reply["update_available"] is False
    pretty = finish(tmp_path, __version__, "--repaired", pretty=True).stdout
    assert pretty.startswith(f"✓ djlib {__version__} installed again  with song recognition")
    assert "Release notes" not in pretty  # the same release
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: 1)
    warning = json.loads(finish(tmp_path, __version__, "--repaired").stdout)["warnings"][0]
    assert warning.startswith("1 assistant session is still running the djlib from before")
    assert warning.endswith("so it uses the new install.")
