"""`djlib upgrade`: install the newest GitHub release with uv, then restart the service."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces.client import VERSION, version_key
from djlib.interfaces.envelope import envelope

REPOSITORY = "uneasymusings/dj-library-tool"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"
WHEEL = re.compile(r"^dj_library_tool-(?P<version>[^-]+)-py3-none-any\.whl$")
# Updating the engine doesn't update the plugin (docs/AGENTS.md).
PLUGIN_UPDATE = (
    "claude plugin marketplace update dj-library-tool && claude plugin update djlib@dj-library-tool"
)
MCP_SERVE = re.compile(r"\bdjlib\b.*\smcp\s+serve\b")


def claude_plugin_version() -> str | None:
    """The newest djlib plugin Claude Code has installed, or None. Reads folder names only."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    folder = root / "plugins" / "cache" / "dj-library-tool" / "djlib"
    try:
        found = [path.name for path in folder.iterdir() if VERSION.match(path.name)]
    except OSError:
        return None
    return max(found, key=version_key, default=None)


def assistant_sessions() -> int:
    """How many `djlib mcp serve` processes are running; 0 where `ps` can't tell (Windows)."""
    if os.name == "nt":
        return 0
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in listing.stdout.splitlines() if MCP_SERVE.search(line))


def update_claude_plugin() -> str | None:
    """Update the Claude Code plugin with the `claude` command: None when done, else why not."""
    claude = shutil.which("claude")
    if not claude:
        return "the `claude` command isn't on PATH"
    for args in (
        ["plugin", "marketplace", "update", "dj-library-tool"],
        ["plugin", "update", "djlib@dj-library-tool"],
    ):
        try:
            done = subprocess.run([claude, *args], capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            return "`claude` didn't respond"
        if done.returncode != 0:
            lines = (done.stderr or done.stdout or "").strip().splitlines()
            return (lines or ["it failed"])[-1][:200]
    return None


def sync_plugin(reply: dict, target: str) -> None:
    """Keep the Claude Code plugin on the engine's release, so tools and skill match it."""
    plugin = claude_plugin_version()
    if not plugin or version_key(plugin) >= version_key(target):
        return
    problem = update_claude_plugin()
    if problem is None:
        reply["result"]["plugin_updated"] = {"from": plugin, "to": target}
        reply["warnings"].append(
            f"Updated the Claude Code plugin from {plugin}. Restart Claude Code (or run "
            "/mcp → djlib → Reconnect) to load it."
        )
        return
    reply["warnings"].append(
        f"The Claude Code plugin is still {plugin} ({problem}). Update it with "
        f"`{PLUGIN_UPDATE}`, then restart Claude Code."
    )


def latest_release() -> dict:
    """The newest published release (pre-releases included) and its wheel URL."""
    import httpx

    try:
        reply = httpx.get(RELEASES, timeout=15, follow_redirects=True)
        reply.raise_for_status()
        releases = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "UPDATE_CHECK_FAILED", "Couldn't reach GitHub to look for a newer djlib.", 502, True
        ) from exc
    for release in releases if isinstance(releases, list) else []:
        if release.get("draft"):
            continue
        for asset in release.get("assets") or []:
            if match := WHEEL.match(asset.get("name") or ""):
                return {
                    "version": match["version"],
                    "tag": release.get("tag_name"),
                    "url": asset.get("browser_download_url"),
                    "notes": release.get("html_url"),
                }
    raise AppError("UPDATE_CHECK_FAILED", "No published djlib release was found.", 502, True)


def installed_by_uv() -> bool:
    """Whether this djlib lives in a `uv tool` environment (so uv can replace it)."""
    return "/uv/tools/" in sys.prefix.replace("\\", "/") or "uv-tools" in sys.prefix


def register_upgrade(app, client, emit, handled, panel=None):
    @app.command("upgrade", rich_help_panel=panel)
    @handled
    def upgrade(
        ctx: typer.Context,
        check: bool = typer.Option(False, "--check", help="Only say whether a newer djlib exists."),
    ) -> None:
        """Install the newest djlib release and update the plugin.

        Restarts the background service; the Claude Code plugin is updated to match.
        """
        latest = latest_release()
        newer = version_key(latest["version"]) > version_key(__version__)
        result = {"current": __version__, "latest": latest["version"], "notes": latest["notes"]}
        if check or not newer:
            reply = envelope({**result, "update_available": newer, "updated": False})
            if not check:
                sync_plugin(reply, __version__)
            emit(reply)
            return
        uv = shutil.which("uv")
        command = [
            "uv",
            "tool",
            "install",
            "--force",
            "--python",
            "3.13",
            f"dj-library-tool[download] @ {latest['url']}",
        ]
        if not uv or not installed_by_uv():
            raise AppError(
                "UPDATE_MANUAL",
                "This djlib wasn't installed with uv tool, so update it the way you installed "
                f"it. The new release is {latest['version']}: {latest['url']}",
            )
        # The old service would refuse the new client until restarted; jobs stay saved.
        local = client(ctx)
        if local.discover():
            local.request("POST", "/shutdown")
        completed = subprocess.run([uv, *command[1:]], capture_output=True, text=True)
        if completed.returncode != 0:
            raise AppError(
                "UPDATE_FAILED",
                "uv couldn't install the new release: "
                + (completed.stderr.strip().splitlines() or ["unknown error"])[-1][:300],
            )
        # Assistant sessions keep the old djlib in memory until their host restarts them.
        sessions = assistant_sessions()
        reply = envelope(
            {**result, "update_available": True, "updated": True, "assistant_sessions": sessions}
        )
        if sessions:
            count = f"{sessions} assistant session" + (" is" if sessions == 1 else "s are")
            reply["warnings"].append(
                f"{count} still running djlib {__version__}. Restart each one (in Claude Code: "
                f"/mcp → djlib → Reconnect; in Codex: restart Codex) so it uses "
                f"{latest['version']}."
            )
        sync_plugin(reply, latest["version"])
        emit(reply)
