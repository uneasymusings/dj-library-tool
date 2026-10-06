"""`djlib rekordbox …`: put crates into rekordbox quickly, then read its analysis in the background.

Only the import itself uses rekordbox's window (a few seconds per crate). Confirming the
playlist reads rekordbox's menu without focusing it, and BPM/cues come from rekordbox's
analysis files, so nothing polls the UI. The CLI runs in the user's desktop session, which
macOS lets drive another app once allowed; the background coordinator never touches the UI.
"""

import json
import re
import shutil
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from djlib.domain.errors import AppError
from djlib.interfaces import terminal
from djlib.interfaces.service import envelope


def playlist_file_name(name: str) -> str:
    """Keep the collection's name (rekordbox uses it); drop only characters files can't hold."""
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', " ", name).strip(" .")
    return " ".join(cleaned.split())[:120] or "djlib playlist"


def rekordbox_playlists() -> set[str] | None:
    """Playlist names if rekordbox can be read quietly right now, else None (never launches it)."""
    import sys

    if sys.platform != "darwin":
        return None
    from djlib.native import rekordbox_mac as ui

    try:
        ui.ensure_supported()
        return ui.playlists()
    except AppError:
        return None


def find_usb() -> Path:
    """The one external, writable USB volume, preferring one with a rekordbox library."""
    import plistlib
    import subprocess

    candidates = []
    for volume in Path("/Volumes").iterdir():
        try:
            info = plistlib.loads(
                subprocess.run(
                    ["diskutil", "info", "-plist", str(volume)], capture_output=True, timeout=10
                ).stdout
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        if info.get("Internal", True) or info.get("BusProtocol") == "Disk Image":
            continue
        if not info.get("WritableVolume", False):
            continue
        candidates.append(volume)
    with_library = [v for v in candidates if (v / "PIONEER").is_dir()]
    chosen = with_library or candidates
    if len(chosen) != 1:
        names = ", ".join(v.name for v in candidates) or "none"
        raise AppError(
            "DEVICE_REQUIRED",
            f"Plug in one USB stick or pass --device (found: {names}).",
        )
    return chosen[0]


def register_rekordbox(app, client, emit, handled, panel=None):
    rekordbox = typer.Typer(
        help="Put crates into rekordbox and read its BPM/cue analysis in the background.",
        no_args_is_help=True,
    )
    app.add_typer(rekordbox, name="rekordbox", rich_help_panel=panel)

    def status(message: str):
        term = terminal.current()
        if term.json:
            return nullcontext()
        return term.err.status(f"[bold]{message}", spinner_style="accent")

    def finished(local, job: dict) -> dict:
        while job["state"] in {"queued", "running"}:
            job = local.wait(job["job_id"], 60)["result"]
        return job

    def prepare(local, workspace, collection_id: str) -> dict:
        """Hash-check the collection and write `<name>.m3u8` (rekordbox names it after the file)."""
        page = local.request("GET", f"/collections/{collection_id}", params={"limit": 1})
        name, revision = page["result"]["name"], page["result"]["revision"]
        job = finished(
            local,
            local.request(
                "POST",
                "/exports",
                data={
                    "collection_id": collection_id,
                    "idempotency_key": f"rekordbox-push:{collection_id}:{revision}",
                },
            )["result"],
        )
        if job["state"] != "completed":
            raise AppError(
                "EXPORT_FAILED",
                f"The collection could not be exported; see djlib jobs get {job['job_id']}.",
            )
        manifest = json.loads(Path(job["result"]["manifest_path"]).read_text(encoding="utf-8"))
        playlist = workspace.exports / "rekordbox" / f"{playlist_file_name(name)}.m3u8"
        playlist.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job["result"]["playlist_path"], playlist)
        tracks = manifest["collection"]["tracks"]
        return {
            "collection_id": collection_id,
            "playlist": playlist.stem,
            "playlist_file": str(playlist),
            "paths": [track["path"] for track in tracks],
            "tracks": [
                {
                    "sha256": track["sha256"],
                    "size_bytes": (track.get("properties") or {}).get("size_bytes"),
                    "label": f"{track['artist']} - {track['title']}",
                }
                for track in tracks
            ],
        }

    def export_xml(workspace, ui) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = workspace.incoming / "rekordbox"
        for old in sorted(folder.glob("collection-*.xml"))[:-2]:
            old.unlink(missing_ok=True)  # keep only the latest exports
        with status("Exporting rekordbox's collection XML…"):
            return ui.export_collection(folder / f"collection-{stamp}.xml")

    @rekordbox.command("push")
    @handled
    def push(
        ctx: typer.Context,
        collection_ids: Annotated[
            list[str] | None, typer.Argument(help="Collections to push.")
        ] = None,
        request: str | None = typer.Option(
            None, "--request", help="Build a crate from this request list's owned songs first."
        ),
        verify: bool = typer.Option(
            False, "--verify", help="Also export rekordbox's XML once to check members and key."
        ),
        when_idle: int = typer.Option(
            0,
            "--when-idle",
            min=0,
            help="Wait until the keyboard and mouse have been idle this many seconds.",
        ),
    ) -> None:
        """Put collections into rekordbox as playlists, in seconds.

        Uses rekordbox's own File > Import > Import Playlist with your original files, so
        tracks rekordbox already knows keep their analysis and cues. BPM and cues arrive in
        the background as rekordbox analyzes (djlib rekordbox sync). macOS must allow your
        terminal under Privacy & Security > Accessibility.
        """
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        local, workspace = client(ctx), ctx.obj
        ids = list(collection_ids or [])
        if request:
            with status("Building a crate from the request list…"):
                current = local.request("GET", f"/requests/{request}", params={"limit": 1})
                job = finished(
                    local,
                    local.request(
                        "POST",
                        f"/requests/{request}/collection",
                        data={"revision": current["result"]["revision"]},
                    )["result"],
                )
            if job["state"] != "completed":
                raise AppError("COLLECTION_FAILED", "The crate could not be built.")
            ids.append(job["result"]["collection_id"])
        if not ids:
            raise AppError("INPUT_INVALID", "Name a collection ID, or pass --request ID.")
        with status("Checking the collections' files…"):
            crates = [prepare(local, workspace, collection_id) for collection_id in ids]

        if when_idle:
            with status(f"Waiting until you've been away for {when_idle} s…"):
                ui.wait_until_idle(when_idle)
        started = time.monotonic()
        existing = ui.playlists()
        if existing is None:
            from djlib.exporting.native_rekordbox import playlist_names

            # Learn current playlists from one export so a crate is never imported twice.
            existing = playlist_names(export_xml(workspace, ui))
        for crate in crates:
            if crate["playlist"] in existing:
                crate["status"] = "already_in_rekordbox"
                continue
            with status(f"Importing “{crate['playlist']}” in rekordbox…"):
                ui.import_playlist(Path(crate["playlist_file"]))
            crate["status"] = "imported"
        present = ui.playlists()
        # rekordbox's playlist menu is only readable while a track is selected; without it,
        # one XML export (File menu, always available) confirms the import instead.
        verify = verify or present is None
        for crate in crates:
            crate["playlist_found"] = present is not None and crate["playlist"] in present
        reports = {}
        if verify:
            from djlib.exporting.native_rekordbox import playlist_report

            xml = export_xml(workspace, ui)
            for crate in crates:
                reports[crate["collection_id"]] = playlist_report(
                    xml, crate["playlist"], crate["paths"]
                )
                crate["playlist_found"] = reports[crate["collection_id"]]["playlist_found"]
            local.request("POST", "/analysis/rekordbox", data={"path": str(xml)})
        ui_seconds = round(time.monotonic() - started, 1)
        missing = [crate["playlist"] for crate in crates if not crate["playlist_found"]]
        if missing:
            raise AppError(
                "APP_IMPORT_NOT_FOUND",
                "rekordbox has no playlist named " + ", ".join(f"“{m}”" for m in missing) + ".",
            )
        analysis = sync(local)
        for crate in crates:
            crate.pop("paths")
            crate.pop("tracks", None)
            if crate["collection_id"] in reports:
                report = reports[crate["collection_id"]]
                crate.update({k: report[k] for k in ("entries", "expected", "matched", "analyzed")})
        emit(
            envelope(
                {
                    "crates": crates,
                    "rekordbox_ui_seconds": ui_seconds,
                    "analysis_sync": analysis,
                    "verified_by": "rekordbox_xml_export" if verify else "rekordbox_menu",
                    "database_modified_directly": False,
                }
            )
        )

    @rekordbox.command("usb")
    @handled
    def usb(
        ctx: typer.Context,
        collection_id: str,
        device: Path | None = typer.Option(
            None, "--device", help="Mounted USB, e.g. /Volumes/RICARDO_AM (found automatically)."
        ),
        timeout: int = typer.Option(
            600, min=10, help="Seconds to wait for you to select the playlist in rekordbox."
        ),
    ) -> None:
        """Export a crate to a USB stick through rekordbox, then verify every file on it.

        rekordbox's browser cannot be scripted, so you click the playlist once when asked;
        djlib checks it is the right one, runs Playlist > Export Playlist > your USB, waits for
        rekordbox to finish and confirms each track on the stick byte for byte.
        """
        from djlib.exporting.usb_check import check_playlist, library_state, wait_for_copy
        from djlib.native import rekordbox_mac as ui

        # This command waits for the user anyway, so a locked screen just means "not yet".
        with status("Waiting for you to unlock your Mac…"):
            ui.wait_for_unlock(timeout)
        ui.ensure_supported()
        local, workspace = client(ctx), ctx.obj
        volume = device.expanduser().absolute() if device else find_usb()
        crate = prepare(local, workspace, collection_id)
        manifest_tracks = crate.pop("tracks")
        existing = ui.playlists()
        if existing is None:
            from djlib.exporting.native_rekordbox import playlist_names

            existing = playlist_names(export_xml(workspace, ui))
        if crate["playlist"] not in existing:
            with status(f"Importing “{crate['playlist']}” in rekordbox…"):
                ui.import_playlist(Path(crate["playlist_file"]))
        before = library_state(volume)
        term = terminal.current()

        def wrong(name):
            if not term.json:
                term.err.print(
                    f"[warn]That's “{name or 'not a playlist'}”.[/] Click “{crate['playlist']}”."
                )

        if not term.json:
            term.err.print(
                f"[accent]→[/] In rekordbox, click the playlist [bold]{crate['playlist']}[/]. "
                "djlib exports it as soon as it is selected."
            )
        with status(f"Waiting for “{crate['playlist']}” to be selected in rekordbox…"):
            ui.wait_for_selection(volume.name, crate["playlist"], timeout, on_wrong=wrong)
        started = time.monotonic()
        message = f"rekordbox is exporting to {volume.name}…"
        with status(message) as live:

            def progress(count):
                if live is not None:
                    live.update(f"[bold]{message}[/] {count} of {len(manifest_tracks)} files")

            wait_for_copy(volume, manifest_tracks, before, on_progress=progress)
        with status("Checking the playlist on the USB…"):
            check = check_playlist(volume, crate["playlist"], manifest_tracks)
        emit(
            envelope(
                {
                    "collection_id": collection_id,
                    "playlist": crate["playlist"],
                    "device": str(volume),
                    "library_updated": library_state(volume) != before,
                    "export_seconds": round(time.monotonic() - started, 1),
                    **{
                        k: check.get(k)
                        for k in (
                            "expected",
                            "found",
                            "missing",
                            "playlist_on_device",
                            "device_entries",
                            "in_order",
                            "matched_by",
                            "verified_by",
                        )
                    },
                    "player_playback_verified": False,
                    "database_modified_directly": False,
                }
            )
        )

    def sync(local) -> dict | None:
        try:
            return local.request("POST", "/analysis/rekordbox", data={"path": None})["result"]
        except AppError as error:
            if error.code == "NATIVE_ANALYSIS_UNAVAILABLE":
                return None
            raise

    @rekordbox.command("sync")
    @handled
    def sync_command(ctx: typer.Context) -> None:
        """Read rekordbox's analysis files for BPM and cues. No window, no database access."""
        emit(client(ctx).request("POST", "/analysis/rekordbox", data={"path": None}))

    @rekordbox.command("pull")
    @handled
    def pull(
        ctx: typer.Context,
        when_idle: int = typer.Option(
            0,
            "--when-idle",
            min=0,
            help="Wait until the keyboard and mouse have been idle this many seconds.",
        ),
    ) -> None:
        """Export rekordbox's collection XML once to also bring in musical key (uses its window)."""
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        if when_idle:
            with status(f"Waiting until you've been away for {when_idle} s…"):
                ui.wait_until_idle(when_idle)
        xml = export_xml(ctx.obj, ui)
        emit(client(ctx).request("POST", "/analysis/rekordbox", data={"path": str(xml)}))
