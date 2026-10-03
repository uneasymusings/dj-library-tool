"""Agent-friendly CLI. JSON output and exit codes are the public interface."""

import importlib.util
import json
import shutil
from functools import wraps
from pathlib import Path

import typer
from pydantic import ValidationError

from djlib import __version__
from djlib.domain.contracts import CollectionRequest, DownloadRequest
from djlib.domain.errors import AppError
from djlib.interfaces.client import LocalClient, default_workspace
from djlib.interfaces.service import envelope
from djlib.workspace import Workspace

app = typer.Typer(help="Build traceable DJ music collections locally.", no_args_is_help=True)
jobs = typer.Typer(help="Inspect and control durable jobs.")
reviews = typer.Typer(help="Resolve explicit metadata conflicts.")
service = typer.Typer(help="Manage the local coordinator.")
mcp = typer.Typer(help="Expose the same use cases over MCP stdio.")
app.add_typer(jobs, name="jobs")
app.add_typer(reviews, name="reviews")
app.add_typer(service, name="service")
app.add_typer(mcp, name="mcp")


def handled(function):
    """Keep actionable failures machine-readable, without logging private input."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (AppError, ValidationError, OSError, ValueError) as exc:
            error = (
                exc
                if isinstance(exc, AppError)
                else AppError("INPUT_INVALID", "Check input JSON, paths, and the command schema.")
            )
            emit(envelope(error=error.as_dict()))
            raise typer.Exit(code=2) from exc

    return wrapped


def emit(value: dict) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def client(ctx: typer.Context) -> LocalClient:
    return LocalClient(ctx.obj)


@app.callback()
def configure(
    ctx: typer.Context,
    workspace: Path = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root; defaults to DJLIB_WORKSPACE or user data directory.",
    ),
) -> None:
    ctx.obj = Workspace(workspace or default_workspace())


@app.command()
@handled
def init(ctx: typer.Context, allow_root: list[Path] = typer.Option(None, "--allow-root")) -> None:
    """Initialize an empty workspace and explicitly allow existing music folders."""
    config = ctx.obj.initialize(allow_root)
    emit(envelope({"workspace": str(ctx.obj.root), "config": config.model_dump(mode="json")}))


@app.command()
def version() -> None:
    """Print the application version without starting a service."""
    emit(envelope({"version": __version__}))


@app.command()
@handled
def demo(ctx: typer.Context) -> None:
    """Create original tones and run the local collection-to-export workflow."""
    from djlib.application.demo import run_demo

    emit(envelope(run_demo(ctx.obj)))


@app.command()
def schemas() -> None:
    """Print the strict JSON input schemas without starting a coordinator."""
    from djlib.domain.contracts import (
        ControlRequest,
        DeviceRequest,
        ExportRequest,
        Profile,
        ResolveRequest,
        ScanRequest,
        SourceRequest,
        StartRequest,
    )

    emit(
        envelope(
            {
                contract.__name__: contract.model_json_schema()
                for contract in (
                    CollectionRequest,
                    DownloadRequest,
                    ControlRequest,
                    Profile,
                    ScanRequest,
                    SourceRequest,
                    DeviceRequest,
                    ExportRequest,
                    ResolveRequest,
                    StartRequest,
                )
            }
        )
    )


@app.command()
@handled
def capabilities(ctx: typer.Context) -> None:
    """Discover implemented and planned capabilities."""
    emit(client(ctx).request("GET", "/capabilities"))


@app.command()
@handled
def doctor(ctx: typer.Context) -> None:
    """Report local prerequisites without changing DJ apps or devices."""
    config = ctx.obj.config()
    emit(
        envelope(
            {
                "workspace_id": config.workspace_id,
                "workspace": str(ctx.obj.root),
                "ffmpeg": shutil.which("ffmpeg"),
                "ffprobe": shutil.which("ffprobe"),
                "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
                "deno": shutil.which("deno"),
                "coordinator_url": client(ctx).discover(),
                "native_app_compatibility": "not_verified",
            }
        )
    )


@app.command()
@handled
def plan(ctx: typer.Context, file: Path = typer.Option(..., "--file")) -> None:
    """Plan a collection from JSON containing local paths and requested identities."""
    request = CollectionRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/plans", data=request.model_dump(mode="json")))


@app.command()
@handled
def start(
    ctx: typer.Context,
    plan_id: str,
    revision: int = typer.Option(1, "--revision"),
    key: str = typer.Option(..., "--key"),
) -> None:
    """Submit the reviewed plan; keep the same key when recovering an uncertain reply."""
    emit(
        client(ctx).request(
            "POST", "/jobs", data={"plan_id": plan_id, "revision": revision, "idempotency_key": key}
        )
    )


@app.command()
@handled
def scan(ctx: typer.Context, path: Path, key: str = typer.Option(..., "--key")) -> None:
    """Index existing audio in place. Does not rename files or infer acoustic identities."""
    emit(client(ctx).request("POST", "/scans", data={"path": str(path), "idempotency_key": key}))


@app.command()
@handled
def download(ctx: typer.Context, file: Path = typer.Option(..., "--file")) -> None:
    """Queue selected public recording URLs. Input supplies identity and an idempotency key."""
    request = DownloadRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/downloads", data=request.model_dump(mode="json")))


@app.command("source-inspect")
@handled
def source_inspect(ctx: typer.Context, url: str) -> None:
    """Read a set's publisher metadata/chapters; this does not identify its audio."""
    emit(client(ctx).request("POST", "/sources/inspect", data={"url": url}))


@app.command()
@handled
def library(
    ctx: typer.Context, query: str = "", limit: int = typer.Option(20, min=1, max=100)
) -> None:
    """Search catalog labels with bounded results."""
    emit(client(ctx).request("GET", "/library", params={"query": query, "limit": limit}))


@app.command()
@handled
def collection(
    ctx: typer.Context,
    collection_id: str,
    after: int = typer.Option(0, min=0),
    limit: int = typer.Option(50, min=1, max=100),
) -> None:
    """Read a collection page; next_cursor supplies the next --after value."""
    emit(
        client(ctx).request(
            "GET", f"/collections/{collection_id}", params={"after": after, "limit": limit}
        )
    )


@app.command()
@handled
def export(ctx: typer.Context, collection_id: str, key: str = typer.Option(..., "--key")) -> None:
    """Prepare a hash-checked manifest, M3U, and experimental rekordbox XML."""
    emit(
        client(ctx).request(
            "POST", "/exports", data={"collection_id": collection_id, "idempotency_key": key}
        )
    )


@app.command("usb-preflight")
@handled
def usb_preflight(
    ctx: typer.Context, path: Path, required_bytes: int = typer.Option(0, min=0)
) -> None:
    """Inspect mounted storage and capacity without writing to it."""
    emit(
        client(ctx).request(
            "POST", "/devices/preflight", data={"path": str(path), "required_bytes": required_bytes}
        )
    )


@jobs.command("list")
@handled
def job_list(ctx: typer.Context, limit: int = typer.Option(20, min=1, max=100)) -> None:
    emit(client(ctx).request("GET", "/jobs", params={"limit": limit}))


@jobs.command("get")
@handled
def job_get(ctx: typer.Context, job_id: str) -> None:
    emit(client(ctx).request("GET", f"/jobs/{job_id}"))


@jobs.command("wait")
@handled
def job_wait(
    ctx: typer.Context, job_id: str, timeout: float = typer.Option(30, min=0, max=60)
) -> None:
    """Wait briefly; timeout leaves the job running and exits with code 3."""
    reply = client(ctx).wait(job_id, timeout)
    emit(reply)
    result = reply["result"]
    if result.get("timed_out"):
        raise typer.Exit(3)
    if (
        result["state"] in {"failed", "cancelled", "needs_attention"}
        or result.get("outcome") == "completed_with_gaps"
    ):
        raise typer.Exit(4)


@jobs.command("items")
@handled
def job_items(
    ctx: typer.Context,
    job_id: str,
    after: int = -1,
    limit: int = typer.Option(20, min=1, max=100),
    state: str | None = None,
) -> None:
    params = {"after": after, "limit": limit}
    if state:
        params["state"] = state
    emit(client(ctx).request("GET", f"/jobs/{job_id}/items", params=params))


@jobs.command("events")
@handled
def job_events(ctx: typer.Context, job_id: str, after: int = typer.Option(0, min=0)) -> None:
    emit(client(ctx).request("GET", f"/jobs/{job_id}/events", params={"after": after}))


@jobs.command("control")
@handled
def job_control(ctx: typer.Context, job_id: str, action: str) -> None:
    """Pause, resume, cancel, or retry a job; retries preserve successful items."""
    emit(client(ctx).request("POST", f"/jobs/{job_id}/control", data={"action": action}))


@reviews.command("list")
@handled
def review_list(ctx: typer.Context, job_id: str | None = None) -> None:
    emit(client(ctx).request("GET", "/reviews", params={"job_id": job_id} if job_id else {}))


@reviews.command("resolve")
@handled
def review_resolve(
    ctx: typer.Context,
    review_id: str,
    revision: int = typer.Option(...),
    choice: str = typer.Option(...),
) -> None:
    """Resolve using accept_requested, use_file_metadata, or skip at a specific revision."""
    emit(
        client(ctx).request(
            "POST", f"/reviews/{review_id}", data={"revision": revision, "choice": choice}
        )
    )


@service.command("status")
@handled
def service_status(ctx: typer.Context) -> None:
    emit(envelope({"url": client(ctx).discover()}))


@service.command("stop")
@handled
def service_stop(ctx: typer.Context) -> None:
    local = client(ctx)
    if local.discover():
        emit(local.request("POST", "/shutdown"))
    else:
        emit(envelope({"state": "not_running"}))


@mcp.command("serve")
@handled
def mcp_serve(ctx: typer.Context) -> None:
    """Run MCP stdio; stdout is reserved for protocol messages."""
    from djlib.interfaces.mcp_server import build_server

    ctx.obj.config()
    build_server(ctx.obj).run(transport="stdio")


if __name__ == "__main__":
    app()
