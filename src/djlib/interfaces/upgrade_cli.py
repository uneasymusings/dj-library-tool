"""`djlib upgrade`: install the newest GitHub release with uv, then restart the service."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces import terminal
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
WINDOWS = os.name == "nt"  # no `ps` there


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
    if WINDOWS:
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


def recognition_installed() -> bool:
    """Whether song recognition imports here. djlib 0.1.0a14's upgrade installed newer
    releases without it (no `recognize` extra, Python 3.13), so upgrade repairs that."""
    from djlib.application.identification import shazam_installed

    return shazam_installed()


def installed_by_uv() -> bool:
    """Whether this djlib lives in a `uv tool` environment (so uv can replace it)."""
    return "/uv/tools/" in sys.prefix.replace("\\", "/") or "uv-tools" in sys.prefix


def finish_in_new_djlib(
    workspace: Path, *, latest: dict, repair: bool, restart: bool, json_output: bool
) -> int:
    """Run the newly installed djlib to finish the upgrade; its exit code.

    uv has just replaced this process's environment, so any module it hasn't imported yet
    may be gone (rich's Unicode tables were, once, and the upgrade ended in a traceback).
    This uses only what is already loaded; the new djlib syncs the plugin, restarts the
    service and prints the result. The tool environment keeps its path, so ``sys.executable``
    is the new Python, as when the service is started.
    """
    command = [
        sys.executable,
        "-m",
        "djlib.interfaces.cli",
        "--workspace",
        str(workspace),
        *(["--json"] if json_output else []),
        "upgrade",
        "--finished-from",
        __version__,
        "--notes",
        str(latest.get("notes") or ""),
        *(["--repaired"] if repair else []),
        *(["--restart-service"] if restart else []),
    ]
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        return subprocess.run(command).returncode
    except OSError:
        # Installed, but the new djlib can't be started from here: say so with what's loaded.
        if json_output:
            reply = envelope(
                {
                    "current": __version__,
                    "latest": latest["version"],
                    "notes": latest.get("notes"),
                    "update_available": not repair,
                    "updated": True,
                }
            )
            sys.stdout.write(json.dumps(reply, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(
                f"djlib {latest['version']} is installed (was {__version__}). "
                "Run djlib doctor to check it.\n"
            )
        return 0


def finished(ctx, client, previous: str, notes: str, repaired: bool, restart: bool) -> dict:
    """The reply of an upgrade, written by the djlib it just installed."""
    # Assistant sessions keep the old djlib in memory until their host restarts them.
    sessions = assistant_sessions()
    reply = envelope(
        {
            "current": previous,
            "latest": __version__,
            "notes": notes or None,
            "update_available": version_key(__version__) > version_key(previous),
            "updated": True,
            "assistant_sessions": sessions,
            **({"repaired": "song recognition"} if repaired else {}),
        }
    )
    if sessions:
        count = f"{sessions} assistant session" + (" is" if sessions == 1 else "s are")
        old, new = f"djlib {previous}", __version__
        if previous == __version__:
            old, new = "the djlib from before this install", "the new install"
        reply["warnings"].append(
            f"{count} still running {old}. Restart each one (in Claude Code: "
            f"/mcp → djlib → Reconnect; in Codex: restart Codex) so it uses {new}."
        )
    if restart:
        # The old service was stopped before the install. Any request starts this version's
        # in its place, and replaces the old one if it is somehow still there.
        try:
            client(ctx).request("GET", "/roots")
            reply["result"]["service_restarted"] = True
        except AppError:
            reply["result"]["service_restarted"] = False
            reply["warnings"].append(
                "The background service didn't start again; the next djlib command starts it."
            )
    sync_plugin(reply, __version__)
    return reply


def register_upgrade(app, client, emit, handled, panel=None):
    @app.command("upgrade", rich_help_panel=panel)
    @handled
    def upgrade(
        ctx: typer.Context,
        check: bool = typer.Option(False, "--check", help="Only say whether a newer djlib exists."),
        # The finishing step, run by the djlib that installed this one (finish_in_new_djlib).
        # Keep these options: older releases pass them to the release they install.
        finished_from: str | None = typer.Option(None, "--finished-from", hidden=True),
        notes: str = typer.Option("", "--notes", hidden=True),
        repaired: bool = typer.Option(False, "--repaired", hidden=True),
        restart: bool = typer.Option(False, "--restart-service", hidden=True),
    ) -> None:
        """Install the newest djlib release and update the plugin.

        Restarts the background service; the Claude Code plugin is updated to match.
        """
        if finished_from is not None:
            emit(finished(ctx, client, finished_from, notes, repaired, restart))
            return
        latest = latest_release()
        newer = version_key(latest["version"]) > version_key(__version__)
        result = {"current": __version__, "latest": latest["version"], "notes": latest["notes"]}
        uv = shutil.which("uv")
        # The same release again, with song recognition, when an earlier upgrade left it out.
        repair = not newer and not recognition_installed() and bool(uv) and installed_by_uv()
        if check or not (newer or repair):
            reply = envelope({**result, "update_available": newer, "updated": False})
            if check:
                reply["result"]["recognition_installed"] = recognition_installed()
            else:
                sync_plugin(reply, __version__)
            emit(reply)
            return
        command = [
            "uv",
            "tool",
            "install",
            "--force",
            # Song recognition (shazamio) has macOS and Windows wheels for 3.12, not 3.13.
            "--python",
            "3.12",
            f"dj-library-tool[download,recognize] @ {latest['url']}",
        ]
        if not uv or not installed_by_uv():
            raise AppError(
                "UPDATE_MANUAL",
                "This djlib wasn't installed with uv tool, so update it the way you installed "
                f"it. The new release is {latest['version']}: {latest['url']}",
            )
        # The old service would refuse the new client until restarted; jobs stay saved.
        local = client(ctx)
        running = bool(local.discover())
        if running:
            local.request("POST", "/shutdown")
        json_output = terminal.current().json
        completed = subprocess.run([uv, *command[1:]], capture_output=True, text=True)
        if completed.returncode != 0:
            raise AppError(
                "UPDATE_FAILED",
                "uv couldn't install the new release: "
                + (completed.stderr.strip().splitlines() or ["unknown error"])[-1][:300],
            )
        # From here on this process imports nothing: the new djlib finishes the upgrade.
        code = finish_in_new_djlib(
            ctx.obj.root, latest=latest, repair=repair, restart=running, json_output=json_output
        )
        raise typer.Exit(code)
