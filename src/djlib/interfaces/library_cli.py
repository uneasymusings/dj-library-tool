"""CLI commands for catalog requests, annotations, and ordered collections."""

import hashlib
from pathlib import Path

import typer

from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest
from djlib.domain.request_contracts import RequestCreate, RequestResolution


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
            from djlib.application.tracklists import parse_tracklist

            content = text.read_text(encoding="utf-8")
            items, skipped = parse_tracklist(content, source)
            from djlib.application.tracklists import HEADING

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
        reply = client(ctx).request("POST", "/requests", data=body.model_dump(mode="json"))
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
        emit(client(ctx).request("POST", f"/requests/{request_id}/refresh", data=body))

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
