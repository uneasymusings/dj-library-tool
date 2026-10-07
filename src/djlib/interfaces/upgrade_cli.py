"""`djlib upgrade`: install the newest GitHub release with uv, then restart the service."""

import re
import shutil
import subprocess
import sys

import typer

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces.client import version_key
from djlib.interfaces.envelope import envelope

REPOSITORY = "uneasymusings/dj-library-tool"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"
WHEEL = re.compile(r"^dj_library_tool-(?P<version>[^-]+)-py3-none-any\.whl$")


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
        """Install the newest djlib release (and restart the service)."""
        latest = latest_release()
        newer = version_key(latest["version"]) > version_key(__version__)
        result = {"current": __version__, "latest": latest["version"], "notes": latest["notes"]}
        if check or not newer:
            emit(envelope({**result, "update_available": newer, "updated": False}))
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
        emit(envelope({**result, "update_available": True, "updated": True}))
