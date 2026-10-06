"""CLI commands for catalog requests, annotations, and ordered collections."""

import hashlib
from pathlib import Path

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
    """A request list from a plain tracklist file, plus warnings for skipped lines."""
    from djlib.application.tracklists import HEADING, parse_tracklist

    try:
        content = text.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AppError("INPUT_INVALID", f"Could not read {text} as a UTF-8 text file.") from exc
    items, skipped = parse_tracklist(content, source)
    headings = [line for _, line, reason in skipped if reason == HEADING]
    warnings = [
        f"line {number} skipped ({reason}): {line}"
        for number, line, reason in skipped
        if reason != HEADING
    ]
    if not items:
        raise AppError("INPUT_INVALID", "No “Artist - Title” lines were found.")
    # A heading such as "Set Zero — Friday" names the list.
    title = name or (headings[0][:300] if headings else text.stem)
    digest = hashlib.sha256(f"{title}\n{source}\n{content}".encode()).hexdigest()[:16]
    body = RequestCreate(name=title, items=items, idempotency_key=f"tracklist:{digest}")
    return body, warnings


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


def register_commands(app, client, emit, handled, panel=None):
    requests = typer.Typer(
        help="Track wanted songs: what you own, what is missing, what is unknown.",
        no_args_is_help=True,
    )
    organize = typer.Typer(
        help="Add DJ notes to tracks and build ordered collections.", no_args_is_help=True
    )
    app.add_typer(requests, name="requests", rich_help_panel=panel)
    app.add_typer(organize, name="organize", rich_help_panel=panel)

    @requests.command("list")
    @handled
    def request_list(
        ctx: typer.Context,
        query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
        limit: int = typer.Option(20, min=1, max=100),
        after: str | None = typer.Option(None, help="Cursor from the previous page."),
    ):
        """List saved request lists."""
        params = {"query": query, "limit": limit}
        if after is not None:
            params["after"] = after
        emit(client(ctx).request("GET", "/requests", params=params))

    @requests.command("create")
    @handled
    def request_create(
        ctx: typer.Context,
        file: Path | None = typer.Option(None, help="JSON request list (see: djlib schemas)."),
        text: Path | None = typer.Option(
            None, help="Plain tracklist, one “Artist - Title (Mix)” per line."
        ),
        name: str | None = typer.Option(None, help="List name for --text; defaults to the file."),
        source: str | None = typer.Option(
            None, help="HTTPS link to the set, kept as evidence for unknown IDs in --text."
        ),
    ):
        """Save a list of wanted songs and check which ones you already own.

        Paste a tracklist into a text file and pass --text; no JSON needed.
        """
        if (file is None) == (text is None):
            raise AppError("INPUT_INVALID", "Pass either --file JSON or --text tracklist.")
        warnings: list[str] = []
        if file is not None:
            body = RequestCreate.model_validate_json(file.read_text(encoding="utf-8"))
        else:
            body, warnings = tracklist_request(text, name, source)
        local = client(ctx)
        reply = finish_checks(
            local, local.request("POST", "/requests", data=body.model_dump(mode="json"))
        )
        reply["warnings"] = [*reply.get("warnings", []), *warnings]
        emit(reply)

    @requests.command("collect")
    @handled
    def request_collect(
        ctx: typer.Context,
        request_id: str,
        name: str | None = typer.Option(None, help="Collection name; defaults to the list's."),
        revision: int | None = typer.Option(
            None, min=1, help="Request revision; defaults to the current one."
        ),
    ):
        """Turn the songs you own from a request list into a collection, in list order."""
        local = client(ctx)
        if revision is None:
            current = local.request("GET", f"/requests/{request_id}", params={"limit": 1})
            revision = current["result"]["revision"]
        body = {"revision": revision}
        if name is not None:
            body["name"] = name
        emit(local.request("POST", f"/requests/{request_id}/collection", data=body))

    @requests.command("get")
    @handled
    def request_get(
        ctx: typer.Context,
        request_id: str,
        after: int = typer.Option(0, min=0),
        limit: int = typer.Option(100, min=1, max=100),
    ):
        """Show a request list and each song's status."""
        emit(
            client(ctx).request(
                "GET", f"/requests/{request_id}", params={"after": after, "limit": limit}
            )
        )

    @requests.command("refresh")
    @handled
    def request_refresh(
        ctx: typer.Context,
        request_id: str,
        revision: int = typer.Option(..., min=1),
        item_id: list[str] = typer.Option(None, "--item-id"),
    ):
        """Re-check a request list against the catalog, e.g. after a scan."""
        body = {"revision": revision}
        if item_id is not None:
            body["item_ids"] = item_id
        local = client(ctx)
        emit(
            finish_checks(
                local, local.request("POST", f"/requests/{request_id}/refresh", data=body)
            )
        )

    @requests.command("resolve")
    @handled
    def request_resolve(
        ctx: typer.Context, request_id: str, item_id: str, file: Path = typer.Option(...)
    ):
        """Pick a source or catalog match for one requested song."""
        body = RequestResolution.model_validate_json(file.read_text(encoding="utf-8"))
        emit(
            client(ctx).request(
                "POST", f"/requests/{request_id}/items/{item_id}", data=body.model_dump(mode="json")
            )
        )

    @requests.command("report")
    @handled
    def request_report(
        ctx: typer.Context, request_id: str, revision: int = typer.Option(..., min=1)
    ):
        """Save a missing/ambiguous report for a request list."""
        emit(
            client(ctx).request(
                "POST", f"/requests/{request_id}/report", data={"revision": revision}
            )
        )

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
