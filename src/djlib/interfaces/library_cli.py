"""JSON CLI commands for catalog requests, annotations, and ordered collections."""

from pathlib import Path

import typer

from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest
from djlib.domain.request_contracts import RequestCreate, RequestResolution


def register_commands(app, client, emit, handled):
    requests = typer.Typer(help="Track exact recordings, owned matches and unresolved IDs.")
    organize = typer.Typer(help="Keep DJ notes and make ordered collections from catalog evidence.")
    app.add_typer(requests, name="requests")
    app.add_typer(organize, name="organize")

    @requests.command("list")
    @handled
    def request_list(
        ctx: typer.Context,
        query: str = typer.Option(""),
        limit: int = typer.Option(20, min=1, max=100),
        after: str | None = typer.Option(None),
    ):
        """Find saved song requests without remembered IDs."""
        params = {"query": query, "limit": limit}
        if after is not None:
            params["after"] = after
        emit(client(ctx).request("GET", "/requests", params=params))

    @requests.command("create")
    @handled
    def request_create(ctx: typer.Context, file: Path = typer.Option(...)):
        body = RequestCreate.model_validate_json(file.read_text(encoding="utf-8"))
        emit(client(ctx).request("POST", "/requests", data=body.model_dump(mode="json")))

    @requests.command("get")
    @handled
    def request_get(
        ctx: typer.Context,
        request_id: str,
        after: int = typer.Option(0, min=0),
        limit: int = typer.Option(100, min=1, max=100),
    ):
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
        body = {"revision": revision}
        if item_id is not None:
            body["item_ids"] = item_id
        emit(client(ctx).request("POST", f"/requests/{request_id}/refresh", data=body))

    @requests.command("resolve")
    @handled
    def request_resolve(
        ctx: typer.Context, request_id: str, item_id: str, file: Path = typer.Option(...)
    ):
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
        body = AnnotationRequest.model_validate_json(file.read_text(encoding="utf-8"))
        emit(
            client(ctx).request(
                "POST", "/annotations", data=body.model_dump(mode="json", exclude_unset=True)
            )
        )

    @organize.command("collection")
    @handled
    def organization(ctx: typer.Context, file: Path = typer.Option(...)):
        body = OrganizationRequest.model_validate_json(file.read_text(encoding="utf-8"))
        emit(client(ctx).request("POST", "/organization", data=body.model_dump(mode="json")))
