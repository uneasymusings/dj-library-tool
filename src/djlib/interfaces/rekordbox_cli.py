"""`djlib rekordbox …`: push a crate into rekordbox and read its own analysis back.

The CLI runs in the user's desktop session, which macOS lets drive other apps once allowed;
the background coordinator never touches the UI.
"""

import json
import shutil
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import typer

from djlib.domain.errors import AppError
from djlib.interfaces import terminal
from djlib.interfaces.service import envelope


def register_rekordbox(app, client, emit, handled, panel=None):
    rekordbox = typer.Typer(
        help="Drive rekordbox: push crates in as playlists and pull its BPM/key analysis.",
        no_args_is_help=True,
    )
    app.add_typer(rekordbox, name="rekordbox", rich_help_panel=panel)

    def status(message: str):
        term = terminal.current()
        if term.json:
            return nullcontext()
        return term.err.status(f"[bold]{message}", spinner_style="accent")

    def export_xml(workspace, ui) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = workspace.incoming / "rekordbox" / f"collection-{stamp}.xml"
        with status("Exporting rekordbox's collection XML…"):
            return ui.export_collection(destination)

    @rekordbox.command("push")
    @handled
    def push(
        ctx: typer.Context,
        collection_id: str,
        wait: int = typer.Option(
            600, min=0, help="Seconds to wait for rekordbox to finish analyzing the tracks."
        ),
    ) -> None:
        """Import a collection into rekordbox as a playlist, wait for analysis, read it back.

        Uses rekordbox's own File menu (Import Playlist, Export Collection in xml format);
        the playlist points at your original files, so tracks rekordbox already has keep
        their analysis and cues. macOS must allow your terminal under Accessibility.
        """
        from djlib.exporting.delivery_media import safe_name
        from djlib.exporting.native_rekordbox import playlist_report
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        local, workspace = client(ctx), ctx.obj
        page = local.request("GET", f"/collections/{collection_id}", params={"limit": 1})
        name, revision = page["result"]["name"], page["result"]["revision"]
        with status("Checking the collection's files…"):
            job = local.request(
                "POST",
                "/exports",
                data={
                    "collection_id": collection_id,
                    "idempotency_key": f"rekordbox-push:{collection_id}:{revision}",
                },
            )["result"]
            while job["state"] in {"queued", "running"}:
                job = local.wait(job["job_id"], 60)["result"]
        if job["state"] != "completed":
            raise AppError(
                "EXPORT_FAILED",
                f"The collection could not be exported; see djlib jobs get {job['job_id']}.",
            )
        manifest = json.loads(Path(job["result"]["manifest_path"]).read_text(encoding="utf-8"))
        paths = [track["path"] for track in manifest["collection"]["tracks"]]
        # rekordbox names an imported playlist after the file.
        playlist = workspace.exports / "rekordbox" / f"{safe_name(name)}.m3u8"
        playlist.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job["result"]["playlist_path"], playlist)

        with status("Importing the playlist in rekordbox…"):
            ui.import_playlist(playlist)
        deadline = time.monotonic() + wait
        while True:
            xml = export_xml(workspace, ui)
            report = playlist_report(xml, playlist.stem, paths)
            complete = report["playlist_found"] and report["matched"] == report["expected"]
            if (complete and report["analyzed"] >= report["matched"]) or (
                time.monotonic() >= deadline
            ):
                break
            with status(
                f"Waiting for rekordbox analysis  {report['analyzed']}/{report['expected']}"
            ):
                time.sleep(20)
        if not report["playlist_found"]:
            raise AppError(
                "APP_IMPORT_NOT_FOUND",
                f"rekordbox's export has no playlist named “{playlist.stem}”. "
                "Check for an open dialog in rekordbox, then retry.",
            )
        analysis = local.request("POST", "/analysis/rekordbox", data={"path": str(xml)})["result"]
        emit(
            envelope(
                {
                    **report,
                    "collection_id": collection_id,
                    "playlist_file": str(playlist),
                    "analysis_import": {
                        key: analysis.get(key)
                        for key in ("matched", "updated", "unchanged", "kept_your_values")
                    },
                    "verified_by": "rekordbox_xml_export",
                    "database_modified_directly": False,
                }
            )
        )

    @rekordbox.command("pull")
    @handled
    def pull(ctx: typer.Context) -> None:
        """Export rekordbox's collection and bring its BPM/key analysis into the catalog."""
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        xml = export_xml(ctx.obj, ui)
        emit(client(ctx).request("POST", "/analysis/rekordbox", data={"path": str(xml)}))
