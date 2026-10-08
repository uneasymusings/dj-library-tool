import json
import os
import subprocess
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

    monkeypatch.setattr(upgrade_cli.subprocess, "run", ps)
    assert upgrade_cli.assistant_sessions() == 2
    assert calls == [["ps", "-axo", "pid=,args="]]

    def missing(command, **kwargs):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", missing)
    assert upgrade_cli.assistant_sessions() == 0
    monkeypatch.setattr(upgrade_cli.os, "name", "nt")
    monkeypatch.setattr(upgrade_cli.subprocess, "run", lambda *a, **kw: pytest.fail("ps on nt"))
    assert upgrade_cli.assistant_sessions() == 0


@pytest.mark.parametrize("sessions", [0, 1, 2])
def test_upgrade_says_which_sessions_and_plugin_still_run_the_old_djlib(
    tmp_path, monkeypatch, sessions
):
    latest = {"version": "99.0.0", "tag": "v99.0.0", "url": "https://e/w.whl", "notes": "n"}
    monkeypatch.setattr(upgrade_cli, "latest_release", lambda: latest)
    # No `claude` on PATH: the plugin can't be updated here, so the user is told how.
    monkeypatch.setattr(
        upgrade_cli.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None
    )
    monkeypatch.setattr(upgrade_cli, "installed_by_uv", lambda: True)
    monkeypatch.setattr(LocalClient, "discover", lambda self: None)
    installs = []

    def uv(command, **kwargs):
        installs.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", uv)
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: sessions)
    plugin_cache("0.1.0a12")
    reply = CliRunner().invoke(cli_app, ["--workspace", str(tmp_path / "w"), "upgrade"])
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    assert envelope["result"]["updated"] is True and len(installs) == 1
    assert envelope["result"]["assistant_sessions"] == sessions
    warnings = envelope["warnings"]
    assert len(warnings) == 1 + bool(sessions)
    assert warnings[-1].startswith("The Claude Code plugin is still 0.1.0a12")
    assert upgrade_cli.PLUGIN_UPDATE in warnings[-1]
    if sessions:
        count = "1 assistant session is" if sessions == 1 else "2 assistant sessions are"
        assert warnings[0].startswith(f"{count} still running djlib {__version__}")
        assert "/mcp → djlib → Reconnect" in warnings[0] and "uses 99.0.0" in warnings[0]


def test_upgrade_brings_the_claude_code_plugin_along(tmp_path, monkeypatch):
    """Engine and plugin from different releases broke the tools once; upgrade syncs both."""
    latest = {"version": "99.0.0", "tag": "v99.0.0", "url": "https://e/w.whl", "notes": "n"}
    monkeypatch.setattr(upgrade_cli, "latest_release", lambda: latest)
    monkeypatch.setattr(upgrade_cli.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(upgrade_cli, "installed_by_uv", lambda: True)
    monkeypatch.setattr(upgrade_cli, "assistant_sessions", lambda: 0)
    monkeypatch.setattr(LocalClient, "discover", lambda self: None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(upgrade_cli.subprocess, "run", run)
    plugin_cache("0.1.0a12")
    workspace = ["--workspace", str(tmp_path / "w")]
    reply = CliRunner().invoke(cli_app, [*workspace, "upgrade"])
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    assert envelope["result"]["plugin_updated"] == {"from": "0.1.0a12", "to": "99.0.0"}
    assert commands[1:] == [
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
