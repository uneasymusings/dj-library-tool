"""Authenticated loopback coordinator; one process owns each workspace catalog."""

import argparse
import asyncio
import contextlib
import os
import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from filelock import FileLock
from starlette.middleware.trustedhost import TrustedHostMiddleware

from djlib import __version__
from djlib.application.service import Application, new_id
from djlib.domain.contracts import (
    CollectionRequest,
    ControlRequest,
    DeliveryDeviceRequest,
    DeliveryNativeXMLRequest,
    DeliveryObservation,
    DeliveryPrepareRequest,
    DeliveryRequest,
    DeliveryVerifyRequest,
    DeviceRequest,
    DownloadRequest,
    ExportRequest,
    ResolveRequest,
    ScanRequest,
    SourceRequest,
    StartRequest,
)
from djlib.domain.errors import AppError
from djlib.domain.workspace_contracts import RootsRequest
from djlib.exporting.handoff import device_preflight
from djlib.interfaces.validation import validation_message
from djlib.jobs.worker import Worker
from djlib.persistence.database import Database
from djlib.sources.web import inspect_source
from djlib.workspace import Workspace, atomic_json


def envelope(result: dict | None = None, error: dict | None = None) -> dict:
    return {
        "schema_version": "1",
        "ok": error is None,
        "request_id": new_id("req"),
        "result": result,
        "warnings": [],
        "error": error,
    }


def create_app(workspace: Workspace, instance_id: str, *, run_worker: bool = True) -> FastAPI:
    database = Database(workspace.database)
    database.migrate()
    application = Application(workspace, database)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(Worker(application).run()) if run_worker else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        database.engine.dispose()

    async def authorized(request: Request, authorization: str | None = Header(default=None)):
        expected = f"Bearer {workspace.token()}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise AppError("AUTH_REQUIRED", "A valid local service token is required.", 401)
        origin = request.headers.get("origin")
        if origin and origin != f"http://{request.headers.get('host')}":
            raise AppError("ORIGIN_DENIED", "This request has an unauthorized browser origin.", 403)

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(authorized)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.state.application = application

    @app.exception_handler(AppError)
    async def application_error(_request, exc: AppError):
        return JSONResponse(envelope(error=exc.as_dict()), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        return JSONResponse(
            envelope(
                error={
                    "code": "INPUT_INVALID",
                    "message": validation_message(exc),
                    "retryable": False,
                }
            ),
            status_code=422,
        )

    @app.get("/health")
    async def health():
        return envelope(
            {
                "instance_id": instance_id,
                "workspace_id": workspace.config().workspace_id,
                "protocol_version": "1",
                "pid": os.getpid(),
                "application_version": __version__,
            }
        )

    @app.get("/capabilities")
    async def capabilities():
        return envelope(application.capabilities())

    @app.get("/roots")
    async def roots():
        config = workspace.config()
        return envelope(
            {"workspace_id": config.workspace_id, "allowed_roots": config.allowed_roots}
        )

    @app.post("/roots")
    async def add_roots(body: RootsRequest):
        return envelope(await asyncio.to_thread(workspace.add_roots, [Path(p) for p in body.paths]))

    @app.get("/profiles/{name}")
    async def profile(name: str):
        return envelope(application.profile(name).model_dump(mode="json"))

    @app.post("/plans")
    async def plan(body: CollectionRequest):
        return envelope(application.plan(body))

    @app.get("/plans/{plan_id}")
    async def get_plan(plan_id: str):
        return envelope(application.get_plan(plan_id))

    @app.post("/jobs")
    async def start(body: StartRequest):
        return envelope(application.start(body))

    @app.post("/scans")
    async def scan(body: ScanRequest):
        return envelope(application.scan(body.path, body.idempotency_key))

    @app.post("/downloads")
    async def acquire(body: DownloadRequest):
        return envelope(application.download(body))

    @app.post("/sources/inspect")
    async def source(body: SourceRequest):
        return envelope(await inspect_source(body.url))

    @app.post("/devices/preflight")
    async def preflight(body: DeviceRequest):
        return envelope(await asyncio.to_thread(device_preflight, body.path, body.required_bytes))

    @app.get("/jobs")
    async def jobs(
        limit: int = Query(default=20, ge=1, le=100),
        query: str = Query(default="", max_length=500),
        after: str | None = Query(default=None, max_length=2000),
    ):
        return envelope(application.jobs(limit, query, after))

    @app.get("/jobs/{job_id}")
    async def job(job_id: str):
        return envelope(application.job(job_id))

    @app.get("/jobs/{job_id}/items")
    async def items(
        job_id: str,
        limit: int = Query(default=20, ge=1, le=100),
        after: int = -1,
        state: str | None = None,
    ):
        return envelope(application.items(job_id, limit, after, state))

    @app.get("/jobs/{job_id}/events")
    async def events(
        job_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return envelope(application.events(job_id, after, limit))

    @app.post("/jobs/{job_id}/control")
    async def control(job_id: str, body: ControlRequest):
        return envelope(application.control(job_id, body.action))

    @app.get("/reviews")
    async def reviews(job_id: str | None = None, limit: int = Query(default=20, ge=1, le=100)):
        return envelope(application.reviews(job_id, limit))

    @app.post("/reviews/{review_id}")
    async def resolve(review_id: str, body: ResolveRequest):
        return envelope(application.resolve(review_id, body.revision, body.choice))

    @app.get("/library")
    async def library(
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        after: str | None = Query(default=None, max_length=2000),
    ):
        return envelope(application.library(query, limit, after))

    @app.get("/collections")
    async def collections(
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        after: str | None = Query(default=None, max_length=2000),
    ):
        return envelope(application.saved("collections", query, limit, after))

    @app.get("/requests")
    async def requests(
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        after: str | None = Query(default=None, max_length=2000),
    ):
        return envelope(application.saved("requests", query, limit, after))

    @app.get("/deliveries")
    async def deliveries(
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        after: str | None = Query(default=None, max_length=2000),
    ):
        return envelope(application.saved("deliveries", query, limit, after))

    from djlib.application.reconciliation import reconcile_files
    from djlib.domain.reconciliation_contracts import ReconcileRequest

    @app.post("/reconciliations")
    async def reconcile(body: ReconcileRequest):
        return envelope(reconcile_files(application, body))

    @app.get("/collections/{collection_id}")
    async def collection(
        collection_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        after: int = Query(default=0, ge=0),
    ):
        result = application.collection(collection_id)
        tracks = result["tracks"]
        result["track_count"] = len(tracks)
        from djlib.application.catalog_paging import attach_dj

        result["tracks"] = attach_dj(application, tracks[after : after + limit])
        result["next_cursor"] = after + limit if after + limit < len(tracks) else None
        return envelope(result)

    @app.post("/exports")
    async def export(body: ExportRequest):
        return envelope(application.export(body.collection_id, body.idempotency_key))

    @app.get("/delivery-targets")
    async def delivery_targets():
        from djlib.exporting.app_targets import app_profiles
        from djlib.exporting.targets import target_profiles

        return envelope(
            {
                "targets": target_profiles(),
                "apps": app_profiles(),
                "native_automation_available": False,
            }
        )

    @app.post("/deliveries")
    async def delivery_plan(body: DeliveryRequest):
        from djlib.application.delivery import create_delivery

        return envelope(await asyncio.to_thread(create_delivery, application, body))

    @app.get("/deliveries/{delivery_id}")
    async def delivery_get(delivery_id: str):
        from djlib.application.delivery import delivery_status

        return envelope(await asyncio.to_thread(delivery_status, application, delivery_id))

    @app.post("/deliveries/{delivery_id}/prepare")
    async def delivery_prepare(delivery_id: str, body: DeliveryPrepareRequest):
        from djlib.application.delivery import prepare_delivery

        return envelope(
            prepare_delivery(application, delivery_id, body.revision, body.idempotency_key)
        )

    @app.post("/deliveries/{delivery_id}/device")
    async def delivery_device(delivery_id: str, body: DeliveryDeviceRequest):
        from djlib.application.delivery import bind_device

        return envelope(
            await asyncio.to_thread(bind_device, application, delivery_id, body.revision, body.path)
        )

    @app.post("/deliveries/{delivery_id}/observations")
    async def delivery_observe(delivery_id: str, body: DeliveryObservation):
        from djlib.application.delivery_checks import submit_observation

        return envelope(await asyncio.to_thread(submit_observation, application, delivery_id, body))

    @app.post("/deliveries/{delivery_id}/verify")
    async def delivery_verify(delivery_id: str, body: DeliveryVerifyRequest):
        from djlib.application.delivery import verify_device

        return envelope(
            await asyncio.to_thread(verify_device, application, delivery_id, body.revision)
        )

    @app.post("/deliveries/{delivery_id}/verify-app")
    async def delivery_verify_app(delivery_id: str, body: DeliveryVerifyRequest):
        from djlib.application.delivery_checks import submit_app_verification

        return envelope(
            await asyncio.to_thread(
                submit_app_verification, application, delivery_id, body.revision
            )
        )

    @app.post("/deliveries/{delivery_id}/native-xml")
    async def delivery_native_xml(delivery_id: str, body: DeliveryNativeXMLRequest):
        from djlib.application.delivery import inspect_native_xml

        return envelope(
            await asyncio.to_thread(
                inspect_native_xml, application, delivery_id, body.revision, body.path
            )
        )

    @app.post("/shutdown")
    async def shutdown():
        server = getattr(app.state, "server", None)
        if server:
            asyncio.get_running_loop().call_later(0.2, setattr, server, "should_exit", True)
        return envelope({"state": "stopping"})

    from djlib.interfaces.library_workflows import register_http

    register_http(app, application, envelope)
    return app


async def serve(workspace: Workspace) -> None:
    workspace.config()
    with FileLock(workspace.runtime / "coordinator.lock", timeout=0):
        instance_id = new_id("instance")
        app = create_app(workspace, instance_id)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        app.state.server = server
        atomic_json(
            workspace.runtime / "service.json",
            {
                "url": f"http://127.0.0.1:{port}",
                "instance_id": instance_id,
                "workspace_id": workspace.config().workspace_id,
                "protocol_version": "1",
                "pid": os.getpid(),
            },
        )
        try:
            await server.serve(sockets=[sock])
        finally:
            (workspace.runtime / "service.json").unlink(missing_ok=True)
            sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(Workspace(args.workspace)))


if __name__ == "__main__":
    main()
