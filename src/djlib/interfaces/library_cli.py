"""CLI commands for catalog requests, annotations, and ordered collections."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

import typer

from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest
from djlib.domain.request_contracts import RequestCreate, RequestResolution


def unchecked(items: list[dict]) -> list[str]:
    return [
        item["item_id"]
        for item in items
        if any(c.get("availability") == "verification_limit" for c in item.get("candidates") or [])
    ]


def all_items(local, result: dict) -> list[dict]:
    """Every item of a request list result, following its pages."""
    items, offset = list(result.get("items") or []), result.get("next_offset")
    while offset is not None:
        page = local.request(
            "GET", f"/requests/{result['request_id']}", params={"after": offset, "limit": 100}
        )["result"]
        items += page.get("items") or []
        offset = page.get("next_offset")
    return items


def tracklist_request(
    text: Path, name: str | None, source: str | None
) -> tuple[RequestCreate, list[str]]:
    """A request list from a plain tracklist file (or paste, or - for stdin), plus warnings
    for skipped lines. Spreadsheet cells (pasted with tabs) or a CSV with Artist and Title
    columns (Shazam's export too) read as a song list, as `djlib set` reads them; pasted
    cells are named after their first song, as `djlib set` names them."""
    from djlib.interfaces.identify_cli import (
        pasted_name,
        read_text_file,
        read_typed,
        tracklist_wanted,
        typed,
    )
    from djlib.sources import shazam_history

    if kind := typed(text):
        link, content = read_typed(kind)
        if link is not None:
            raise tracklist_wanted(kind)
        fallback, columns = pasted_name(content), "\t" in content
    else:
        content, fallback = read_text_file(text), text.stem
        columns = shazam_history.csv_kind(text.expanduser()) in {"songs", "shazam"}
    rows = shazam_history.song_rows(content) if columns else None
    if rows is not None:
        if kind:
            # "Pasted 14 Oct · Antdot - Mirage + 1 more": two pastes a day aren't one name.
            fallback = pasted_name(song_lines(rows))
        return song_list_request(rows, name, fallback)
    return text_request(content, name, fallback, source)


def text_request(
    content: str, name: str | None, fallback: str, source: str | None
) -> tuple[RequestCreate, list[str]]:
    """A request list from tracklist text; ``fallback`` names it when it has no heading."""
    from djlib.application.tracklists import HEADING, parse_tracklist
    from djlib.domain.contracts import safe_text

    items, skipped = parse_tracklist(content, source)
    headings = [line for _, line, reason in skipped if reason == HEADING]
    from djlib.interfaces.identify_cli import quoted

    warnings = [
        f"line {number} skipped ({reason}): “{quoted(line)}”"
        for number, line, reason in skipped
        if reason != HEADING
    ]
    if not items:
        raise AppError("INPUT_INVALID", "No “Artist - Title” lines were found.")
    # A heading such as "Set Zero — Friday" names the list.
    title = name or (headings[0][:300] if headings else " ".join(safe_text(fallback).split())[:300])
    # Keyed by what was parsed, so rerunning a file reuses its list, and a file parsed
    # differently (edited, or by a newer djlib) becomes a new list instead of a conflict.
    parsed = json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True)
    digest = hashlib.sha256(f"{title}\n{source}\n{parsed}".encode()).hexdigest()[:16]
    body = RequestCreate(name=title, items=items, idempotency_key=f"tracklist:{digest}")
    return body, warnings


def song_lines(rows: list[dict]) -> str:
    """A song list's rows as “Artist - Title (Mix)” lines, for what djlib hears to merge."""
    lines = []
    for row in rows:
        mix = f" ({row['version']})" if row["version"] else ""
        lines.append(f"{row['artist']} - {row['title']}{mix}\n")
    return "".join(lines)


def song_list_request(
    rows: list[dict], name: str | None, fallback: str
) -> tuple[RequestCreate, list[str]]:
    """A request list from a spreadsheet's Artist and Title (and Mix) columns, in its order;
    ``fallback`` names it. Versions read as in a Shazam CSV: "Rain (Extended Mix)" has one."""
    from djlib.domain.contracts import safe_text
    from djlib.domain.request_contracts import RequestItem
    from djlib.interfaces.shazam_cli import MAX_SONGS, labels
    from djlib.interfaces.terminal import plural

    items, left_out = [], 0
    for row in rows:
        artist, title, version = labels(row)
        try:
            items.append(
                RequestItem(
                    artist=artist[:500] or None,
                    title=title[:1000] or None,
                    version=(row["version"] or version)[:300],
                )
            )
        except ValueError:
            left_out += 1
    if not items:
        raise AppError("INPUT_INVALID", "No row has both an artist and a title.")
    warnings = []
    if left_out:
        warnings.append(f"Left out {plural(left_out, 'row')} without an artist or title.")
    if len(items) > MAX_SONGS:
        warnings.append(
            f"A list holds {MAX_SONGS} songs, so this one leaves out the last "
            f"{len(items) - MAX_SONGS}."
        )
        items = items[:MAX_SONGS]
    title = name or " ".join(safe_text(fallback).split())[:300]
    parsed = json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True)
    digest = hashlib.sha256(f"{title}\nNone\n{parsed}".encode()).hexdigest()[:16]
    body = RequestCreate(name=title, items=items, idempotency_key=f"tracklist:{digest}")
    return body, warnings


def created(local, body) -> dict:
    """POST a request list; when the same list already exists, re-check it against the library.

    Rerunning an unchanged tracklist after adding music must report the new songs as owned.
    The same set with other notes (a newer djlib, or more heard) updates its list in place.
    """
    reply = local.request(
        "POST", "/requests", data=body.model_dump(mode="json"), params={"same_list": "true"}
    )
    result = reply["result"]
    if result.get("reused"):
        reply = local.request(
            "POST",
            f"/requests/{result['request_id']}/refresh",
            data={"revision": result["revision"]},
        )
    return finish_checks(local, reply)


def finish_checks(local, reply: dict) -> dict:
    """Keep hash-checking matches that hit the per-call budget, in bounded batches.

    Each refresh reads at most 1 GiB for 30 seconds; a large FLAC library needs several.
    Stops when everything is checked or a batch makes no progress (e.g. one huge file).
    """
    from djlib.interfaces import terminal

    term, previous = terminal.current(), None
    while True:
        result = reply["result"]
        pending = unchecked(all_items(local, result))
        if not pending or (previous is not None and len(pending) >= previous):
            return reply
        previous = len(pending)
        body = {"revision": result["revision"], "item_ids": pending[:1000]}
        if term.json:
            reply = local.request("POST", f"/requests/{result['request_id']}/refresh", data=body)
            continue
        with term.err.status(
            f"[bold]Checking files[/]  {len(pending)} matches left", spinner_style="accent"
        ):
            reply = local.request("POST", f"/requests/{result['request_id']}/refresh", data=body)


STORES = {"beatport": "Beatport", "bandcamp": "Bandcamp", "traxsource": "Traxsource"}


def shopping(items: list[dict]) -> dict:
    """A request list as a shopping list, in set order: the missing songs with store searches,
    the songs you own in another mix, and the IDs."""
    from djlib.interfaces.rekordbox_cli import set_track

    tracks = [set_track(item) for item in items]

    def picked(own: str, *keys: str) -> list[dict]:
        return [{key: track[key] for key in keys} for track in tracks if track["own"] == own]

    return {
        "to_buy": picked("missing", "position", "timestamp", "label", "shop"),
        "other_mixes": picked(
            "other_mix", "position", "timestamp", "label", "title", "you_own", "shop"
        ),
        # A file with the same title and mix under other credits: check it before buying.
        "maybe_yours": picked("maybe", "position", "timestamp", "label", "maybe_owned", "shop"),
        "ids": picked("id", "position", "timestamp"),
    }


def moment(song: dict) -> str:
    """Where a song plays in the set: its time, else its number."""
    return song.get("timestamp") or f"#{song.get('position')}"


def shopping_text(name: str, found: dict) -> str:
    """The shopping list as plain text, laid out as `djlib requests report` prints it."""
    from djlib.interfaces.terminal import number
    from djlib.interfaces.views import owned_mixes

    to_buy, others = found["to_buy"], found["other_mixes"]
    maybe = found.get("maybe_yours") or []
    width = max((len(moment(song)) for song in [*to_buy, *others, *maybe]), default=0)
    lines = [f"{name} — {number(len(to_buy))} to buy", ""]
    for song in to_buy:
        lines.append(f"{moment(song):>{width}}  {song['label']}")
        lines += [
            f"{'':>{width}}  {label:<10}  {song['shop'][store]}" for store, label in STORES.items()
        ]
    if maybe:
        lines += ["", f"Maybe yours already: check before you buy ({number(len(maybe))})"]
        for song in maybe:
            lines.append(
                f"{moment(song):>{width}}  {song['label']} — you have: {song['maybe_owned']}"
            )
    if others:
        lines += ["", f"You own another mix ({number(len(others))})"]
        for song in others:
            mixes = owned_mixes(song["you_own"], song.get("title"))
            lines.append(f"{moment(song):>{width}}  {song['label']} — you have: {mixes}")
    if found["ids"]:
        lines += ["", "IDs, no name yet: " + ", ".join(moment(song) for song in found["ids"])]
    return "\n".join(lines) + "\n"


def save_shopping_list(workspace, report_path: str | None, name: str, found: dict) -> str | None:
    """Write "<set> - to buy.txt" beside the request's reports, replacing the last one."""
    import os

    from djlib.interfaces.rekordbox_cli import playlist_file_name

    if not report_path:
        return None
    folder = Path(report_path).parent
    try:
        if not folder.resolve().is_relative_to(workspace.root.resolve()):
            return None
        path = folder / f"{playlist_file_name(name)} - to buy.txt"
        temporary = folder / f".{path.name}.partial"
        temporary.write_text(shopping_text(name, found), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return None
    return str(path)


def built_crate(local, result: dict) -> dict | None:
    """The crate `requests collect` last made from this list, if any.

    Found by the collect job's retry token, which names the list, so sets that share a name
    (every Shazam import is "Shazam") never share a crate.
    """
    owned = int((result.get("counts") or {}).get("satisfied") or 0)
    if not owned or not result.get("name") or not result.get("request_id"):
        return None
    prefix = f"request-collection:{result['request_id']}:"
    params = {"query": str(result["name"])[:500], "limit": 20}
    for job in local.request("GET", "/jobs", params=params)["result"].get("jobs") or []:
        made = job.get("result") if isinstance(job.get("result"), dict) else {}
        key = str(job.get("idempotency_key") or "")
        if job.get("state") == "completed" and key.startswith(prefix) and made.get("collection_id"):
            return {"collection_id": made["collection_id"], "tracks": made.get("selected_count")}
    return None


def current_revision(local, request_id: str) -> int:
    """The request list's revision right now, for commands that default to it."""
    return local.request("GET", f"/requests/{request_id}", params={"limit": 1})["result"][
        "revision"
    ]


def resolution_body(text: str, revision: int | None, current) -> RequestResolution:
    """A resolution from JSON text; ``revision`` (or the current one) fills in a missing one."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return RequestResolution.model_validate_json(text)  # reports what is wrong
    if revision is not None:
        data["revision"] = revision
    elif "revision" not in data:
        data["revision"] = current()
    return RequestResolution.model_validate(data)


def register_commands(app, client, emit, handled, panel=None):
    from djlib.interfaces.handles import resolve

    requests = typer.Typer(
        help="Sets you checked: owned, missing, IDs.",
        no_args_is_help=True,
    )
    organize = typer.Typer(
        help="Add DJ notes to tracks and build ordered collections.", no_args_is_help=True
    )
    app.add_typer(requests, name="requests", rich_help_panel=panel)
    app.add_typer(organize, name="organize", hidden=True)
    request_help = "Set name, ID start (6+ characters) or last."
    # --revision is for scripts and assistants (the JSON has it); a DJ never needs it.
    revision_help = "The set's revision; defaults to the current one."

    def emit_list(local, reply: dict) -> None:
        """A request list; a terminal also learns the crate already made from it."""
        from djlib.interfaces import terminal

        if not terminal.current().json and isinstance(reply.get("result"), dict):
            try:
                crate = built_crate(local, reply["result"])
            except AppError:
                crate = None  # the list still shows; the view offers to build the crate
            if crate:
                reply["result"]["crate"] = crate
        emit(reply)

    @requests.command("create")
    @handled
    def request_create(
        ctx: typer.Context,
        file: Path | None = typer.Option(None, help="The set as JSON (see: djlib schemas)."),
        text: Path | None = typer.Option(
            None,
            help="A tracklist file, one “Artist - Title (Mix)” per line; paste for one you "
            "copied, - for a pipe.",
        ),
        name: str | None = typer.Option(None, help="Set name for --text; defaults to the file."),
        source: str | None = typer.Option(
            None, help="HTTPS link to the set, kept as evidence for unknown IDs in --text."
        ),
    ):
        """Save a set's tracklist and check which songs you already own.

        Copy a tracklist and pass --text paste (or pipe it into --text -); no JSON needed.
        """
        if (file is None) == (text is None):
            raise AppError("INPUT_INVALID", "Pass either --file JSON or --text tracklist.")
        warnings: list[str] = []
        if file is not None:
            body = RequestCreate.model_validate_json(file.read_text(encoding="utf-8"))
        else:
            body, warnings = tracklist_request(text, name, source)
        local = client(ctx)
        reply = created(local, body)
        reply["warnings"] = [*reply.get("warnings", []), *warnings]
        emit_list(local, reply)

    @requests.command("get")
    @handled
    def request_get(
        ctx: typer.Context,
        request_id: Annotated[str, typer.Argument(metavar="SET", help=request_help)],
        after: int = typer.Option(0, min=0),
        limit: int = typer.Option(100, min=1, max=100),
    ):
        """Show a set: each song, owned or missing."""
        from djlib.interfaces.rekordbox_cli import set_track

        local = client(ctx)
        request_id = resolve(local, "request", request_id)
        reply = local.request(
            "GET", f"/requests/{request_id}", params={"after": after, "limit": limit}
        )
        # Store searches for each song you don't own (search links; djlib never opens them).
        for item in reply["result"].get("items") or []:
            if (shop := set_track(item)["shop"]) is not None:
                item["shop"] = shop
        emit_list(local, reply)

    @requests.command("collect")
    @handled
    def request_collect(
        ctx: typer.Context,
        request_id: Annotated[str, typer.Argument(metavar="SET", help=request_help)],
        name: str | None = typer.Option(None, help="Crate name; defaults to the set's."),
        revision: int | None = typer.Option(None, min=1, help=revision_help, hidden=True),
    ):
        """Make a crate of the songs you own in a set, in set order."""
        local = client(ctx)
        request_id = resolve(local, "request", request_id)
        if revision is None:
            revision = current_revision(local, request_id)
        body = {"revision": revision}
        if name is not None:
            body["name"] = name
        emit(local.request("POST", f"/requests/{request_id}/collection", data=body))

    @requests.command("refresh")
    @handled
    def request_refresh(
        ctx: typer.Context,
        request_id: Annotated[str, typer.Argument(metavar="SET", help=request_help)],
        revision: int | None = typer.Option(None, min=1, help=revision_help, hidden=True),
        item_id: list[str] = typer.Option(None, "--item-id", help="Only this song; repeatable."),
    ):
        """Check a set against your music again, e.g. after a scan."""
        local = client(ctx)
        request_id = resolve(local, "request", request_id)
        if revision is None:
            revision = current_revision(local, request_id)
        body = {"revision": revision}
        if item_id is not None:
            body["item_ids"] = item_id
        emit_list(
            local,
            finish_checks(
                local, local.request("POST", f"/requests/{request_id}/refresh", data=body)
            ),
        )

    @requests.command("report")
    @handled
    def request_report(
        ctx: typer.Context,
        request_id: Annotated[str, typer.Argument(metavar="SET", help=request_help)],
        revision: int | None = typer.Option(None, min=1, help=revision_help, hidden=True),
    ):
        """Your shopping list: the missing songs, with Beatport, Bandcamp and Traxsource links.

        Also saved as a text file, beside a full JSON report of the songs not owned.
        """
        local = client(ctx)
        request_id = resolve(local, "request", request_id)
        if revision is None:
            revision = current_revision(local, request_id)
        reply = local.request("POST", f"/requests/{request_id}/report", data={"revision": revision})
        ledger = local.request("GET", f"/requests/{request_id}", params={"limit": 100})["result"]
        found = shopping(all_items(local, ledger))
        name = ledger.get("name") or "Set"
        result = reply["result"]
        result.update(name=name, **found)
        result["shopping_list"] = save_shopping_list(
            ctx.obj, result.get("report_path"), name, found
        )
        emit(reply)

    @requests.command("resolve")
    @handled
    def request_resolve(
        ctx: typer.Context,
        request_id: Annotated[str, typer.Argument(metavar="SET", help=request_help)],
        item_id: str,
        file: Path = typer.Option(..., help="JSON resolution (djlib schemas)."),
        revision: int | None = typer.Option(
            None,
            min=1,
            help="The set's revision; defaults to the file's, else the current one.",
            hidden=True,
        ),
    ):
        """Pick a source or library match for one song in a set."""
        text = file.read_text(encoding="utf-8")
        local = client(ctx)
        request_id = resolve(local, "request", request_id)
        body = resolution_body(text, revision, lambda: current_revision(local, request_id))
        emit_list(
            local,
            local.request(
                "POST", f"/requests/{request_id}/items/{item_id}", data=body.model_dump(mode="json")
            ),
        )

    @requests.command("list")
    @handled
    def request_list(
        ctx: typer.Context,
        query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
        limit: int = typer.Option(20, min=1, max=100),
        after: str | None = typer.Option(None, help="Cursor from the previous page."),
    ):
        """The sets you checked, the one checked last first."""
        params = {"query": query, "limit": limit}
        if after is not None:
            params["after"] = after
        emit(client(ctx).request("GET", "/requests", params=params))

    @organize.command("metadata")
    @handled
    def track_metadata(
        ctx: typer.Context, recording_id: str, asset_revision_id: str = typer.Option(...)
    ):
        """Read a track's embedded BPM, key, genre and comments (unverified tags)."""
        emit(
            client(ctx).request(
                "GET",
                f"/recordings/{recording_id}/metadata",
                params={"asset_revision_id": asset_revision_id},
            )
        )

    @organize.command("get")
    @handled
    def annotations(
        ctx: typer.Context, recording_id: str, asset_revision_id: str = typer.Option(...)
    ):
        """Show your saved notes for a track."""
        emit(
            client(ctx).request(
                "GET",
                f"/recordings/{recording_id}/annotations",
                params={"asset_revision_id": asset_revision_id},
            )
        )

    @organize.command("annotate")
    @handled
    def annotate(ctx: typer.Context, file: Path = typer.Option(...)):
        """Save notes, tags, set role, energy or BPM/key with provenance."""
        body = AnnotationRequest.model_validate_json(file.read_text(encoding="utf-8"))
        emit(
            client(ctx).request(
                "POST", "/annotations", data=body.model_dump(mode="json", exclude_unset=True)
            )
        )

    @organize.command("collection")
    @handled
    def organization(ctx: typer.Context, file: Path = typer.Option(...)):
        """Build an ordered, filtered collection from catalog tracks."""
        body = OrganizationRequest.model_validate_json(file.read_text(encoding="utf-8"))
        emit(client(ctx).request("POST", "/organization", data=body.model_dump(mode="json")))
