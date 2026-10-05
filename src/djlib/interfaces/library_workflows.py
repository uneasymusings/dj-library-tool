"""Shared thin HTTP and MCP adapters for request ledgers and DJ organization."""

import asyncio

from fastapi import Query

from djlib.application.organization import (
    annotate_recording,
    annotation_status,
    inspect_metadata,
    organize_collection,
)
from djlib.application.requests import (
    collect_request,
    create_request,
    export_missing_report,
    get_request,
    refresh_request,
    resolve_request,
)
from djlib.domain.contracts import ResponseEnvelope
from djlib.domain.organization_contracts import (
    AnalysisImport,
    AnnotationRequest,
    OrganizationRequest,
)
from djlib.domain.request_contracts import (
    RequestCollect,
    RequestCreate,
    RequestRefresh,
    RequestResolution,
)


def register_http(app, application, envelope):
    @app.post("/requests")
    async def request_create(body: RequestCreate):
        return envelope(await asyncio.to_thread(create_request, application, body))

    @app.get("/requests/{request_id}")
    async def request_get(
        request_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return envelope(get_request(application, request_id, after, limit))

    @app.post("/requests/{request_id}/refresh")
    async def request_refresh(request_id: str, body: RequestRefresh):
        return envelope(await asyncio.to_thread(refresh_request, application, request_id, body))

    @app.post("/requests/{request_id}/items/{item_id}")
    async def request_resolve(request_id: str, item_id: str, body: RequestResolution):
        return envelope(
            await asyncio.to_thread(resolve_request, application, request_id, item_id, body)
        )

    @app.post("/requests/{request_id}/report")
    async def request_report(request_id: str, body: RequestRefresh):
        return envelope(
            await asyncio.to_thread(export_missing_report, application, request_id, body.revision)
        )

    @app.post("/requests/{request_id}/collection")
    async def request_collect(request_id: str, body: RequestCollect):
        return envelope(
            await asyncio.to_thread(
                collect_request, application, request_id, body.revision, body.name
            )
        )

    @app.post("/analysis/rekordbox")
    async def analysis_import(body: AnalysisImport):
        from djlib.application.native_analysis import import_rekordbox_analysis

        return envelope(await asyncio.to_thread(import_rekordbox_analysis, application, body.path))

    @app.get("/recordings/{recording_id}/metadata")
    async def track_metadata(
        recording_id: str, asset_revision_id: str = Query(min_length=1, max_length=200)
    ):
        return envelope(
            await asyncio.to_thread(inspect_metadata, application, recording_id, asset_revision_id)
        )

    @app.get("/recordings/{recording_id}/annotations")
    async def annotations(
        recording_id: str, asset_revision_id: str = Query(min_length=1, max_length=200)
    ):
        return envelope(annotation_status(application, recording_id, asset_revision_id))

    @app.post("/annotations")
    async def annotate(body: AnnotationRequest):
        return envelope(await asyncio.to_thread(annotate_recording, application, body))

    @app.post("/organization")
    async def organize(body: OrganizationRequest):
        return envelope(await asyncio.to_thread(organize_collection, application, body))


def register_mcp(server, request, read, write, intent):
    @server.tool(structured_output=True, annotations=write)
    async def djlib_create_request(request_body: RequestCreate) -> ResponseEnvelope:
        """Persist an exact-song list and hash-check owned matches; unknown IDs remain explicit."""
        return await request("POST", "/requests", request_body.model_dump(mode="json"))

    @server.tool(structured_output=True, annotations=read)
    async def djlib_request(request_id: str, after: int = 0, limit: int = 100) -> ResponseEnvelope:
        """Page through durable song-request coverage; source selection is not acquisition."""
        return await request(
            "GET", f"/requests/{request_id}", params={"after": after, "limit": limit}
        )

    @server.tool(structured_output=True, annotations=intent)
    async def djlib_refresh_request(
        request_id: str, revision: int, item_ids: list[str] | None = None
    ) -> ResponseEnvelope:
        """Refresh owned matches and availability against the current ledger revision."""
        body = {"revision": revision}
        if item_ids is not None:
            body["item_ids"] = item_ids
        return await request("POST", f"/requests/{request_id}/refresh", body)

    @server.tool(structured_output=True, annotations=intent)
    async def djlib_resolve_request(
        request_id: str, item_id: str, resolution: RequestResolution
    ) -> ResponseEnvelope:
        """Select a source or explicitly satisfy a request with exact catalog identity evidence."""
        return await request(
            "POST", f"/requests/{request_id}/items/{item_id}", resolution.model_dump(mode="json")
        )

    @server.tool(structured_output=True, annotations=read)
    async def djlib_request_report(request_id: str, revision: int) -> ResponseEnvelope:
        """Save a revision-specific missing-track report inside the workspace; no downloads."""
        return await request("POST", f"/requests/{request_id}/report", {"revision": revision})

    @server.tool(structured_output=True, annotations=write)
    async def djlib_collect_request(
        request_id: str, revision: int, name: str | None = None
    ) -> ResponseEnvelope:
        """Queue an ordered collection of the request list's owned tracks, in list order.

        Missing, ambiguous and unknown songs stay in the request. Wait for the returned job,
        then use its collection_id.
        """
        body = {"revision": revision}
        if name is not None:
            body["name"] = name
        return await request("POST", f"/requests/{request_id}/collection", body)

    @server.tool(structured_output=True, annotations=write)
    async def djlib_import_rekordbox_analysis(path: str) -> ResponseEnvelope:
        """Read BPM/key from a rekordbox Collection XML export into catalog annotations.

        Tracks match by exact file location (originals or prepared working copies), values keep
        source rekordbox_analysis and verified false, and operator-chosen values are kept.
        The XML must be inside an allowed folder or the workspace.
        """
        return await request("POST", "/analysis/rekordbox", {"path": path})

    @server.tool(structured_output=True, annotations=read)
    async def djlib_track_metadata(recording_id: str, asset_revision_id: str) -> ResponseEnvelope:
        """Read tagged BPM/key/genre/comment evidence for exact bytes; no acoustic analysis."""
        return await request(
            "GET",
            f"/recordings/{recording_id}/metadata",
            params={"asset_revision_id": asset_revision_id},
        )

    @server.tool(structured_output=True, annotations=read)
    async def djlib_annotations(recording_id: str, asset_revision_id: str) -> ResponseEnvelope:
        """Read stored DJ notes, categories and analysis provenance for one exact byte revision."""
        return await request(
            "GET",
            f"/recordings/{recording_id}/annotations",
            params={"asset_revision_id": asset_revision_id},
        )

    @server.tool(structured_output=True, annotations=write)
    async def djlib_annotate(request_body: AnnotationRequest) -> ResponseEnvelope:
        """Patch revision-checked catalog annotations; preserve original audio and tags."""
        return await request(
            "POST", "/annotations", request_body.model_dump(mode="json", exclude_unset=True)
        )

    @server.tool(structured_output=True, annotations=write)
    async def djlib_organize(request_body: OrganizationRequest) -> ResponseEnvelope:
        """Create an ordered collection from exact catalog IDs with explicit BPM/key/tag filters."""
        return await request("POST", "/organization", request_body.model_dump(mode="json"))
