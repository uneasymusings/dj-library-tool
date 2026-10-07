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
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from djlib.domain.errors import AppError
from djlib.interfaces import terminal
from djlib.interfaces.envelope import envelope
from djlib.interfaces.terminal import plural


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


def history_file(workspace) -> Path:
    return workspace.exports / "rekordbox" / "history.json"


def remembered(workspace) -> dict:
    """What djlib last did with each collection in rekordbox and on USB, by collection ID."""
    try:
        return json.loads(history_file(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(workspace, collection_id: str, **fields) -> None:
    history = remembered(workspace)
    history.setdefault(collection_id, {}).update(fields)
    path = history_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def event(name: str, **fields) -> None:
    """One JSON line on stderr for a waiting step, so a backgrounded caller can prompt the user."""
    import sys

    sys.stderr.write(json.dumps({"event": name, **fields}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


# Reasons a skipped rekordbox step is worth retrying once the user has acted.
RETRY_LATER = frozenset({"APP_SCREEN_LOCKED", "APP_AUTOMATION_NOT_ALLOWED"})
LOCKED_GRACE_SECONDS = 30


def usb_failed(usb: dict | None) -> bool:
    """A USB check that a DJ must not rely on: files missing or out of order."""
    if not usb:
        return False
    return usb.get("found") != usb.get("expected") or usb.get("in_order") is False


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


def set_item(item: dict) -> dict:
    """A request item that did not make the crate, with any other versions you own."""
    from djlib.interfaces.views import mix_name

    source = item.get("input") or {}
    if source.get("kind") == "unknown":
        label = source.get("label") or "Unknown ID"
        if source.get("timestamp"):
            label += f" @ {source['timestamp']}"
    else:
        label = f"{source.get('artist') or 'Unknown artist'} - {source.get('title') or 'Untitled'}"
        if source.get("version"):
            label += f" ({source['version']})"
    others = [
        mix_name(candidate)
        for candidate in item.get("candidates") or []
        if candidate.get("identity_match") == "different_version"
    ]
    return {
        "position": item.get("position"),
        "label": label,
        "state": item.get("state"),
        "you_own": list(dict.fromkeys(others)),
    }


def register_rekordbox(app, client, emit, handled, panel=None, start_panel=None):
    rekordbox = typer.Typer(
        help="Put crates into rekordbox and onto USB.",
        no_args_is_help=True,
    )
    app.add_typer(rekordbox, name="rekordbox", rich_help_panel=panel)

    def status(message: str):
        term = terminal.current()
        if term.json:
            return nullcontext()
        return term.err.status(f"[bold]{escape(message)}", spinner_style="accent")

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
        # A crate keeps the playlist name it got in rekordbox (e.g. "Name (2)"), so later pushes
        # and USB exports find that playlist instead of importing the crate again. Each crate
        # has its own folder, so same-named crates never overwrite each other's file.
        name = remembered(workspace).get(collection_id, {}).get("playlist") or name
        folder = workspace.exports / "rekordbox" / collection_id
        playlist = folder / f"{playlist_file_name(name)}.m3u8"
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

    def build_crate(local, request_id: str, name: str | None = None) -> str:
        """Queue `requests collect` for the current revision and return the collection ID."""
        with status("Building a crate from the request list…"):
            current = local.request("GET", f"/requests/{request_id}", params={"limit": 1})
            body = {"revision": current["result"]["revision"]}
            if name is not None:
                body["name"] = name
            job = finished(
                local,
                local.request("POST", f"/requests/{request_id}/collection", data=body)["result"],
            )
        if job["state"] != "completed":
            raise AppError("COLLECTION_FAILED", "The crate could not be built.")
        return job["result"]["collection_id"]

    def into_rekordbox(local, workspace, ui, crates: list[dict], verify: bool = False) -> dict:
        """Import the crates rekordbox lacks and confirm each playlist exists."""
        started = time.monotonic()
        existing = ui.playlists()
        if existing is None:
            from djlib.exporting.native_rekordbox import playlist_names

            # Learn current playlists from one export so a crate is never imported twice.
            existing = playlist_names(export_xml(workspace, ui))
        history = remembered(workspace)
        # Trust a same-named playlist only if djlib pushed this very crate there; otherwise
        # it may be an older version of the set or the user's own list, so check it first.
        unconfirmed = [
            crate
            for crate in crates
            if crate["playlist"] in existing
            and history.get(crate["collection_id"], {}).get("playlist") != crate["playlist"]
        ]
        # A newer version of a crate djlib pushed before (the set gained or lost songs) goes in
        # as "Name (2)" rather than over the old playlist, which stays for the user to delete.
        ours = {entry.get("playlist") for entry in history.values() if entry.get("playlist")}
        for crate in list(unconfirmed):
            if crate["playlist"] in ours:
                base = crate["playlist"]
                taken = set(existing) | ours
                number = 2
                while f"{base} ({number})" in taken:
                    number += 1
                renamed = Path(crate["playlist_file"]).with_name(
                    f"{playlist_file_name(base)} ({number}).m3u8"
                )
                shutil.copyfile(crate["playlist_file"], renamed)
                # The newest earlier version of this set, which the new playlist supersedes.
                versions = [
                    entry
                    for entry in history.values()
                    if re.fullmatch(re.escape(base) + r"( \(\d+\))?", entry.get("playlist") or "")
                ]
                newest = max(versions, key=lambda entry: entry.get("pushed_at") or "", default={})
                crate.update(
                    replaces=newest.get("playlist") or base,
                    playlist=renamed.stem,
                    playlist_file=str(renamed),
                )
                unconfirmed.remove(crate)
        if unconfirmed:
            from djlib.exporting.native_rekordbox import playlist_report

            xml = export_xml(workspace, ui)
            for crate in unconfirmed:
                report = playlist_report(xml, crate["playlist"], crate["paths"])
                same = report["entries"] == report["matched"] == report["expected"]
                if not (same and report["in_order"]):
                    raise AppError(
                        "APP_PLAYLIST_NAME_TAKEN",
                        f"rekordbox already has a different playlist named “{crate['playlist']}” "
                        f"({report['entries']} tracks, {report['matched']} of this crate's "
                        f"{report['expected']}). Rename or delete it in rekordbox, or give this "
                        "crate another name, then retry. Nothing was imported or exported.",
                    )
        for crate in crates:
            if crate["playlist"] in existing:
                crate["status"] = "already_in_rekordbox"
                continue
            with status(f"Importing “{crate['playlist']}” in rekordbox…"):
                ui.import_playlist(Path(crate["playlist_file"]))
            crate["status"] = "imported"
        present = ui.playlists() if any(c["status"] == "imported" for c in crates) else existing
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
        for crate in crates:
            if crate["collection_id"] in reports:
                report = reports[crate["collection_id"]]
                crate.update({k: report[k] for k in ("entries", "expected", "matched", "analyzed")})
            remember(workspace, crate["collection_id"], playlist=crate["playlist"], pushed_at=now())
        return {
            "rekordbox_ui_seconds": ui_seconds,
            "verified_by": "rekordbox_xml_export" if verify else "rekordbox_menu",
        }

    def to_usb(local, workspace, ui, crate: dict, volume: Path, timeout: int) -> dict:
        """Have rekordbox export one playlist to the stick, then check it as a player would."""
        from djlib.exporting import rekordbox_pdb
        from djlib.exporting.usb_check import check_playlist, library_state, wait_for_copy

        tracks = crate["tracks"]
        name = crate["playlist"]
        before = library_state(volume)
        term = terminal.current()
        instruction = (
            f"In rekordbox's left sidebar, under Playlists, click “{name}” once; "
            f"djlib then exports it to {volume.name} and checks the stick."
        )

        def wrong(selected):
            if term.json:
                event("wrong_playlist", selected=selected, playlist=name)
            else:
                term.err.print(
                    f"[warn]That's “{escape(selected or 'not a playlist')}”.[/] "
                    f"Click “{escape(name)}”."
                )

        def snapshot():
            # The stick as it is right before the export, even if it was replugged meanwhile.
            nonlocal before
            before = library_state(volume)

        def listed() -> bool:
            """Whether the stick's library lists the whole playlist yet (True if unreadable)."""
            try:
                found = rekordbox_pdb.playlist(volume, name)
            except AppError:
                return True
            return found is not None and len(found["tracks"]) >= len(tracks)

        if term.json:
            # Assistants run this in the background; stdout stays one envelope at the end.
            event("select_playlist", playlist=name, device=volume.name, instruction=instruction)
        else:
            term.err.print(f"[accent]→[/] {escape(instruction)}")
        with status(f"Waiting for you to click “{name}” in rekordbox…") as live:

            def waiting(state, seconds_left, selected):
                reason = ui.waiting_reason(state, volume.name, selected)
                if term.json:
                    event(
                        "waiting",
                        state=state,
                        reason=reason,
                        seconds_left=seconds_left,
                        playlist=name,
                    )
                elif live is not None:
                    left = f"{seconds_left // 60}:{seconds_left % 60:02d} left"
                    live.update(
                        f"[bold]Waiting for you to click “{escape(name)}” in rekordbox[/] "
                        f"[muted]({left}; {escape(reason)})[/]"
                    )

            ui.wait_for_selection(
                volume.name,
                name,
                timeout,
                on_wrong=wrong,
                on_state=waiting,
                before_export=snapshot,
            )
        started = time.monotonic()
        message = f"rekordbox is exporting to {volume.name}…"
        with status(message) as live:

            def progress(count):
                if live is not None:
                    live.update(f"[bold]{escape(message)}[/] {count} of {len(tracks)} files")

            wait_for_copy(volume, tracks, before, on_progress=progress, ready=listed)
        with status("Checking the playlist on the USB…"):
            check = check_playlist(volume, name, tracks)
        keys = ("expected", "found", "missing", "playlist_on_device", "device_entries", "in_order")
        analysis = None
        if check.get("device_analysis"):
            from djlib.exporting.usb_check import analysis_xml

            # The stick carries rekordbox's key for each track: bring it into the catalog.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            xml = analysis_xml(
                workspace.incoming / "rekordbox" / f"device-{stamp}.xml",
                check["device_analysis"],
                crate["paths"],
            )
            reply = local.request("POST", "/analysis/rekordbox", data={"path": str(xml)})
            analysis = {
                key: reply["result"].get(key) for key in ("matched", "updated", "kept_your_values")
            }
        remember(
            workspace,
            crate["collection_id"],
            usb={
                "device": volume.name,
                "found": check.get("found"),
                "expected": check.get("expected"),
                "in_order": check.get("in_order"),
                "checked_at": now(),
            },
        )
        return {
            "device": str(volume),
            "library_updated": library_state(volume) != before,
            "export_seconds": round(time.monotonic() - started, 1),
            **{key: check.get(key) for key in (*keys, "matched_by", "verified_by")},
            "analysis_from_device": analysis,
            "player_playback_verified": False,
        }

    def usb_target(device: Path | None) -> Path:
        volume = device.expanduser().absolute() if device else find_usb()
        if not volume.is_dir():
            raise AppError("DEVICE_UNAVAILABLE", f"{volume} is not mounted.")
        return volume

    @rekordbox.command("push")
    @handled
    def push(
        ctx: typer.Context,
        collection_ids: Annotated[
            list[str] | None,
            typer.Argument(help="Crates to push: name, ID or its first characters, or 'last'."),
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
        """Put crates into rekordbox as playlists, in seconds.

        Uses rekordbox's own File > Import > Import Playlist with your original files.

        Tracks rekordbox already knows keep their analysis and cues.

        macOS must allow your terminal under Privacy & Security > Accessibility.
        """
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        from djlib.interfaces.handles import resolve

        local, workspace = client(ctx), ctx.obj
        ids = [resolve(local, "collection", value) for value in collection_ids or []]
        if request:
            ids.append(build_crate(local, resolve(local, "request", request)))
        if not ids:
            raise AppError("INPUT_INVALID", "Name a collection ID, or pass --request ID.")
        with status("Checking the collections' files…"):
            crates = [prepare(local, workspace, collection_id) for collection_id in ids]

        if when_idle:
            with status(f"Waiting until you've been away for {when_idle} s…"):
                ui.wait_until_idle(when_idle)
        outcome = into_rekordbox(local, workspace, ui, crates, verify)
        analysis = sync(local)
        for crate in crates:
            crate.pop("paths")
            crate.pop("tracks")
        emit(
            envelope(
                {
                    "crates": crates,
                    **outcome,
                    "analysis_sync": analysis,
                    "database_modified_directly": False,
                }
            )
        )

    @rekordbox.command("usb")
    @handled
    def usb(
        ctx: typer.Context,
        collection_id: Annotated[
            str, typer.Argument(help="The crate: name, ID or its first characters, or 'last'.")
        ],
        device: Path | None = typer.Option(
            None, "--device", help="Mounted USB, e.g. /Volumes/RICARDO_AM (found automatically)."
        ),
        timeout: int = typer.Option(
            600, min=10, help="Seconds to wait for you to select the playlist in rekordbox."
        ),
    ) -> None:
        """Export a crate to your USB stick through rekordbox, then check it.

        You click the playlist in rekordbox once; djlib checks it is the right one.

        rekordbox exports it, and djlib confirms each track on the stick byte for byte.
        """
        from djlib.native import rekordbox_mac as ui

        await_unlock(ui, timeout)
        ui.ensure_supported()
        from djlib.interfaces.handles import resolve

        local, workspace = client(ctx), ctx.obj
        collection_id = resolve(local, "collection", collection_id)
        volume = usb_target(device)
        with status("Checking the collection's files…"):
            crate = prepare(local, workspace, collection_id)
        into_rekordbox(local, workspace, ui, [crate])
        # Whether the playlist had to be imported first, and which earlier version it replaces.
        imported = {"status": crate["status"], "replaces": crate.get("replaces")}
        result = to_usb(local, workspace, ui, crate, volume, timeout)
        emit(
            envelope(
                {
                    "collection_id": collection_id,
                    "playlist": crate["playlist"],
                    "rekordbox": imported,
                    **result,
                    "database_modified_directly": False,
                }
            )
        )
        if usb_failed(result):
            raise typer.Exit(4)

    @app.command("set", rich_help_panel=start_panel)
    @handled
    def set_command(
        ctx: typer.Context,
        tracklist: Annotated[
            str,
            typer.Argument(
                help="Text file with one “Artist - Title (Mix)” per line, or a YouTube or "
                "SoundCloud set whose description lists its tracks."
            ),
        ],
        name: str | None = typer.Option(
            None, help="Set name; defaults to the tracklist's heading, file name or upload title."
        ),
        source: str | None = typer.Option(
            None, help="With a file: HTTPS link to the set, kept as evidence for unknown IDs."
        ),
        fetch: bool = typer.Option(
            False,
            "--fetch",
            help="Download missing songs as MP3 from YouTube or SoundCloud when the match is "
            "clear (asks first; --yes to skip).",
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="With --fetch: don't ask first."),
        usb: bool = typer.Option(
            False, "--usb", help="Also put it on your USB stick through rekordbox (one click)."
        ),
        when_idle: int = typer.Option(
            0,
            "--when-idle",
            min=0,
            help="Import into rekordbox only once the keyboard and mouse have been idle this "
            "many seconds.",
        ),
        use_rekordbox: bool = typer.Option(
            True,
            "--rekordbox/--no-rekordbox",
            help="Import the crate into rekordbox when it can be driven (macOS); "
            "--no-rekordbox only builds the crate.",
        ),
        device: Path | None = typer.Option(
            None, "--device", help="Mounted USB, e.g. /Volumes/RICARDO_AM (found automatically)."
        ),
        timeout: int = typer.Option(
            600, min=10, help="With --usb: seconds to wait for you to click the playlist."
        ),
    ) -> None:
        """Tracklist → owned songs → rekordbox playlist → USB.

        Takes a text file, or a YouTube/SoundCloud set whose description lists its tracks.

        Matches exact artist/title/version; other versions are never swapped in.

        --fetch downloads clear matches for missing songs as MP3.

        --usb exports the playlist to your stick and checks every file.
        """
        from djlib.interfaces import set_sources
        from djlib.interfaces.library_cli import (
            all_items,
            created,
            finish_checks,
            text_request,
            tracklist_request,
        )
        from djlib.native import rekordbox_mac as ui

        if usb and not use_rekordbox:
            raise AppError("INPUT_INVALID", "--usb exports through rekordbox; drop --no-rekordbox.")
        if yes and not fetch:
            raise AppError("INPUT_INVALID", "--yes only applies with --fetch.")
        if usb and when_idle:
            raise AppError(
                "INPUT_INVALID",
                "--when-idle waits for you to step away, but --usb needs you to click the "
                "playlist; use one or the other.",
            )
        if usb:
            await_unlock(ui, timeout)
            ui.ensure_supported()
        # Owned/missing, the crate and downloads need no DJ app; rekordbox is used when it can be.
        skipped = None if use_rekordbox else ("not requested (--no-rekordbox)", "NOT_REQUESTED")
        if use_rekordbox and not usb:
            skipped = rekordbox_unavailable(ui)
        volume = usb_target(device) if usb else None
        local, workspace = client(ctx), ctx.obj
        term = terminal.current()
        page = None
        if set_sources.is_url(tracklist):
            with status("Reading the set's description and listener comments…"):
                text, page = set_sources.tracklist_from_url(local, tracklist)
            if not text.strip():
                comments = page.get("comments") or []
                raise AppError(
                    "TRACKLIST_NOT_FOUND",
                    "This upload has no tracklist in its description or chapters."
                    + set_sources.named_in_comments(comments)
                    + " Copy its tracklist (for example from 1001Tracklists) into a text file, "
                    f"then run: djlib set FILE --source {tracklist}",
                    details={
                        "source": {
                            k: page.get(k) for k in ("url", "provider", "title", "uploader")
                        },
                        # Listeners' guesses, in set order: a starting point, not a tracklist.
                        "named_in_comments": set_sources.comment_list(comments),
                    },
                )
            body, warnings = text_request(
                text, name, page.get("title") or "Set", page.get("url") or tracklist
            )
        else:
            body, warnings = tracklist_request(Path(tracklist).expanduser(), name, source)
        with status("Checking which songs you own…"):
            reply = created(local, body)
        request = reply["result"]
        items = all_items(local, request)
        fetched = None
        if fetch:
            with status("Searching YouTube and SoundCloud for the missing songs…"):
                chosen, undecided = set_sources.plan_fetch(local, items)
            fetched = {"downloaded": 0, "chosen": chosen, "needs_your_pick": undecided}
            go = bool(chosen) and (yes or confirm_fetch(term, chosen))
            if chosen and not go:
                fetched["skipped"] = "not confirmed; rerun with --yes to download"
            if go:
                body = set_sources.download_body(request["name"], request["request_id"], chosen)
                with status(f"Downloading {plural(len(chosen), 'song')} as MP3…"):
                    job = finished(local, local.request("POST", "/downloads", data=body)["result"])
                    if set_sources.worth_retrying(local, job):
                        # YouTube and SoundCloud refuse a stream now and then; try once more.
                        local.request(
                            "POST", f"/jobs/{job['job_id']}/control", data={"action": "retry"}
                        )
                        job = finished(
                            local, local.request("GET", f"/jobs/{job['job_id']}")["result"]
                        )
                counts = job.get("counts") or {}
                fetched.update(
                    downloaded=int(counts.get("succeeded") or 0),
                    failed=int(counts.get("failed") or 0),
                    job_id=job["job_id"],
                )
                with status("Checking the set again…"):
                    refreshed = local.request(
                        "POST",
                        f"/requests/{request['request_id']}/refresh",
                        data={"revision": request["revision"]},
                    )
                    request = finish_checks(local, refreshed)["result"]
                    items = all_items(local, request)
        owned = [item for item in items if item.get("state") == "satisfied"]
        result = {
            "name": request.get("name"),
            "request_id": request["request_id"],
            "revision": request.get("revision"),
            "source": None
            if page is None
            else {key: page.get(key) for key in ("url", "provider", "title", "uploader")},
            "songs": len(items),
            "owned": len(owned),
            "missing": [set_item(item) for item in items if item.get("state") != "satisfied"],
            "id_hints": set_sources.id_hints(items, (page or {}).get("comments") or []),
            "fetched": fetched,
            "collection_id": None,
            "playlist": None,
            "replaces": None,
            "rekordbox": None,
            "usb": None,
            "database_modified_directly": False,
        }
        if owned:
            collection_id = build_crate(local, request["request_id"])
            with status("Checking the crate's files…"):
                crate = prepare(local, workspace, collection_id)
            result.update(collection_id=collection_id, playlist=crate["playlist"])
            if skipped:
                reason, code = skipped
                result["rekordbox"] = {"status": "skipped", "reason": reason, "reason_code": code}
            else:
                if when_idle:
                    with status(f"Waiting until you've been away for {when_idle} s…"):
                        ui.wait_until_idle(when_idle)
                outcome = into_rekordbox(local, workspace, ui, [crate])
                result["playlist"] = crate["playlist"]
                result["rekordbox"] = {"status": crate["status"], **outcome}
                if crate.get("replaces"):
                    result["rekordbox"]["replaces"] = crate["replaces"]
                    result["replaces"] = crate["replaces"]
            if volume is not None:
                result["usb"] = to_usb(local, workspace, ui, crate, volume, timeout)
        # What is left to do, for people and for assistants that branch on it.
        handle = (result["collection_id"] or "").removeprefix("collection_")[:8]
        unfinished = []
        rekordbox_state = result["rekordbox"] or {}
        if rekordbox_state.get("reason_code") in RETRY_LATER:
            unfinished.append(f"djlib rekordbox push {handle}")
        if usb_failed(result["usb"]):
            unfinished.append(f"djlib rekordbox usb {handle}")
        result["complete"] = not unfinished
        result["next"] = unfinished
        reply = envelope(result)
        reply["warnings"] = [*reply.get("warnings", []), *warnings]
        emit(reply)
        if usb_failed(result["usb"]):
            raise typer.Exit(4)

    def rekordbox_unavailable(ui) -> tuple[str, str] | None:
        """Why rekordbox can't be driven right now (message, code), or None when it can."""
        try:
            ui.ensure_supported()  # macOS only; also checks the lock and Accessibility
        except AppError as error:
            return error.message, error.code
        if ui.installed() is None:
            return "rekordbox is not installed in /Applications", "APP_NOT_INSTALLED"
        return None

    def await_unlock(ui, timeout: int) -> None:
        """A locked Mac can't be driven. In a terminal, wait visibly for the unlock; for
        scripts and assistants, say so at once (stderr event) and give up after a short grace,
        so they can tell the user instead of waiting minutes in silence."""
        if not ui.screen_locked():
            return
        if terminal.current().json:
            event("unlock_mac", reason="macOS does not let apps be controlled while it is locked")
            ui.wait_for_unlock(min(timeout, LOCKED_GRACE_SECONDS))
        else:
            with status("Your Mac is locked; waiting for you to unlock it…"):
                ui.wait_for_unlock(timeout)

    def confirm_fetch(term, chosen: list[dict]) -> bool:
        """Show what would be downloaded and ask; scripts and assistants pass --yes instead."""
        import sys

        if term.json or not sys.stdin.isatty():
            return False
        term.err.print(f"[bold]Found {plural(len(chosen), 'missing song')} to download:[/]")
        for entry in chosen:
            upload = entry["source"]
            length = upload.get("duration")
            minutes = f"{int(length // 60)}:{int(length % 60):02d}" if length else "?"
            term.err.print(
                f"  {escape(entry['label'])}  [muted]← {upload['provider']}: "
                f"“{escape(upload['title'])}” by {escape(upload['uploader'] or '?')} ({minutes})[/]"
            )
        return typer.confirm("Download these as MP3?", default=True, err=True)

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
        """Read BPM and cues from rekordbox's analysis files (no window)."""
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
        """Bring in musical key with one brief rekordbox XML export."""
        from djlib.native import rekordbox_mac as ui

        ui.ensure_supported()
        if when_idle:
            with status(f"Waiting until you've been away for {when_idle} s…"):
                ui.wait_until_idle(when_idle)
        xml = export_xml(ctx.obj, ui)
        emit(client(ctx).request("POST", "/analysis/rekordbox", data={"path": str(xml)}))
