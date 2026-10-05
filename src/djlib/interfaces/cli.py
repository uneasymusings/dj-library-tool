"""djlib CLI. JSON output and exit codes are the public interface for captured output.

A terminal gets readable views instead (see ``terminal.py``); ``--json`` or
``DJLIB_OUTPUT=json`` keeps the envelope in a terminal too.
"""

import importlib.util
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
import typer.core
from pydantic import ValidationError

from djlib import __version__
from djlib.domain.contracts import (
    CollectionRequest,
    DeliveryObservation,
    DeliveryRequest,
    DownloadRequest,
)
from djlib.domain.errors import AppError
from djlib.interfaces import terminal, views
from djlib.interfaces.client import LocalClient, default_workspace
from djlib.interfaces.library_cli import register_commands
from djlib.interfaces.service import envelope
from djlib.interfaces.validation import validation_message
from djlib.workspace import Workspace

START = "Start here"
LIBRARY = "Your library"
BUILD = "Requests and collections"
DELIVER = "DJ app and USB"
RUNTIME = "Jobs and service"
AGENTS = "For assistants and scripts"

EPILOG = (
    "[bold]New here?[/]  "
    "[bold]djlib init --allow-root ~/Music[/]  then  [bold]djlib scan[/]  then  "
    "[bold]djlib library[/]\n\n"
    "Output is JSON when piped or with [bold]--json[/]. "
    "Docs: https://github.com/uneasymusings/dj-library-tool"
)

# Help lists panels in command order, so keep the journey order explicit.
ORDER = [
    *("init", "demo", "doctor", "setup-agent"),
    *("scan", "library", "collections", "collection", "roots", "reviews", "reconcile"),
    *("requests", "organize", "plan", "start", "download", "source-inspect"),
    *("delivery", "export", "usb-preflight"),
    *("jobs", "service"),
    *("mcp", "schemas", "capabilities", "version"),
]


class JourneyGroup(typer.core.TyperGroup):
    def list_commands(self, ctx):
        names = super().list_commands(ctx)
        return sorted(names, key=lambda name: ORDER.index(name) if name in ORDER else len(ORDER))


app = typer.Typer(
    cls=JourneyGroup,
    help=(
        "[bold #f59f00]⣠⣴⣿⣦⣄ djlib[/]  Build DJ collections from the music you own, "
        "then hand them to rekordbox or Serato."
    ),
    epilog=EPILOG,
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
jobs = typer.Typer(help="Watch and control background jobs.", no_args_is_help=True)
reviews = typer.Typer(help="Resolve metadata conflicts found while indexing.", no_args_is_help=True)
service = typer.Typer(help="Start, stop or check the background service.", no_args_is_help=True)
mcp = typer.Typer(help="Serve the same tools to an assistant over MCP stdio.", no_args_is_help=True)
delivery = typer.Typer(
    help="Prepare, record and verify rekordbox/Serato imports and USB delivery.",
    no_args_is_help=True,
)
roots = typer.Typer(help="Show or add the music folders djlib may read.", no_args_is_help=True)
app.add_typer(jobs, name="jobs", rich_help_panel=RUNTIME)
app.add_typer(reviews, name="reviews", rich_help_panel=LIBRARY)
app.add_typer(service, name="service", rich_help_panel=RUNTIME)
app.add_typer(mcp, name="mcp", rich_help_panel=AGENTS)
app.add_typer(delivery, name="delivery", rich_help_panel=DELIVER)
app.add_typer(roots, name="roots", rich_help_panel=LIBRARY)

KEY_HELP = (
    "Idempotency key; resending the same key recovers the same job. "
    "Required with --json; a terminal generates one."
)


def handled(function):
    """Keep actionable failures machine-readable, without logging private input."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        token = terminal.bind(kwargs.get("ctx"))
        try:
            return function(*args, **kwargs)
        except (AppError, ValidationError, OSError, ValueError) as exc:
            error = (
                exc
                if isinstance(exc, AppError)
                else AppError("INPUT_INVALID", validation_message(exc))
                if isinstance(exc, ValidationError)
                else AppError("INPUT_INVALID", "Check input JSON, paths, and the command schema.")
            )
            emit(envelope(error=error.as_dict()))
            raise typer.Exit(code=2) from exc
        finally:
            terminal.unbind(token)

    return wrapped


def emit(value: dict) -> None:
    term = terminal.current()
    if term.json:
        term.print_json(value)
        return
    workspace = _workspace()
    views.show(
        term,
        terminal.command_name(),
        value,
        client_factory=(lambda: LocalClient(workspace)) if workspace else None,
    )


def _workspace() -> Workspace | None:
    ctx = terminal.active_context()
    root = ctx.find_root().obj if ctx is not None else None
    return root if isinstance(root, Workspace) else None


def client(ctx: typer.Context) -> LocalClient:
    return LocalClient(ctx.obj)


def local_path(path: Path) -> str:
    """Resolve here: the background service has its own working directory."""
    return str(path.expanduser().absolute())


def submission_key(key: str | None, kind: str) -> str:
    """Scripts must choose stable keys; an interactive terminal may get a fresh one."""
    if key:
        return key
    if terminal.current().json:
        raise AppError(
            "INPUT_INVALID",
            "Pass --key KEY. A stable idempotency key makes retrying this submission safe.",
        )
    return f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


@app.callback()
def configure(
    ctx: typer.Context,
    workspace: Path = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace folder. Defaults to DJLIB_WORKSPACE or ~/.local/share/djlib/default.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the JSON envelope. This is the default when output is piped.",
    ),
) -> None:
    ctx.obj = Workspace(workspace or default_workspace())
    explicit = workspace is not None and ctx.obj.root != Workspace(default_workspace()).root
    ctx.meta[terminal.META_KEY] = terminal.Terminal(
        json=terminal.wants_json(json_output), workspace=ctx.obj.root if explicit else None
    )


@app.command(rich_help_panel=START)
@handled
def init(
    ctx: typer.Context,
    allow_root: list[Path] = typer.Option(
        None, "--allow-root", help="A music folder djlib may read. Repeat for several."
    ),
) -> None:
    """Create a workspace and allow your music folders.

    djlib only reads inside allowed folders and never moves or retags your originals.
    """
    config = ctx.obj.initialize(allow_root)
    emit(envelope({"workspace": str(ctx.obj.root), "config": config.model_dump(mode="json")}))


@roots.command("list")
@handled
def roots_list(ctx: typer.Context):
    """Show allowed music folders (no filesystem discovery)."""
    emit(client(ctx).request("GET", "/roots"))


@roots.command("add")
@handled
def roots_add(ctx: typer.Context, paths: Annotated[list[Path], typer.Argument()]):
    """Allow more music folders, keeping existing permissions and music unchanged."""
    from djlib.domain.workspace_contracts import RootsRequest

    body = RootsRequest(paths=[local_path(path) for path in paths])
    emit(client(ctx).request("POST", "/roots", data=body.model_dump(mode="json")))


@app.command(rich_help_panel=AGENTS)
@handled
def version(ctx: typer.Context) -> None:
    """Print the version without starting the background service."""
    emit(envelope({"version": __version__}))


@app.command(rich_help_panel=START)
@handled
def setup_agent(
    ctx: typer.Context,
    output: Path = typer.Option(..., "--output", help="New folder for the session."),
) -> None:
    """Create a Claude Code or Codex session wired to this workspace.

    The session folder gets the djlib skill, explicit MCP configuration and launch.py.
    """
    from djlib.application.agent_setup import create_agent_session

    emit(envelope(create_agent_session(ctx.obj, output)))


@app.command(rich_help_panel=START)
@handled
def demo(ctx: typer.Context) -> None:
    """Try the whole workflow on three generated tones.

    Uses no accounts, real music, DJ app databases or USB devices.
    """
    from djlib.application.demo import run_demo

    term = terminal.current()
    if term.json:
        emit(envelope(run_demo(ctx.obj)))
        return
    with term.err.status("[bold]Running the demo…", spinner_style="accent"):
        result = run_demo(ctx.obj)
    emit(envelope(result))


@app.command(rich_help_panel=AGENTS)
@handled
def schemas(ctx: typer.Context) -> None:
    """Print the strict JSON input schemas without starting the service."""
    from djlib.domain.contracts import (
        ControlRequest,
        DeliveryDeviceRequest,
        DeliveryNativeXMLRequest,
        DeliveryPrepareRequest,
        DeliveryVerifyRequest,
        DeviceRequest,
        ExportRequest,
        Profile,
        ResolveRequest,
        ScanRequest,
        SourceRequest,
        StartRequest,
    )
    from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest
    from djlib.domain.reconciliation_contracts import ReconcileRequest
    from djlib.domain.request_contracts import RequestCreate, RequestRefresh, RequestResolution
    from djlib.domain.workspace_contracts import RootsRequest

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
                    DeliveryRequest,
                    DeliveryPrepareRequest,
                    DeliveryDeviceRequest,
                    DeliveryObservation,
                    DeliveryVerifyRequest,
                    DeliveryNativeXMLRequest,
                    AnnotationRequest,
                    OrganizationRequest,
                    RequestCreate,
                    RequestRefresh,
                    RequestResolution,
                    ReconcileRequest,
                    RootsRequest,
                )
            }
        )
    )


@app.command(rich_help_panel=AGENTS)
@handled
def capabilities(ctx: typer.Context) -> None:
    """List implemented and planned capabilities."""
    emit(client(ctx).request("GET", "/capabilities"))


@app.command(rich_help_panel=START)
@handled
def doctor(ctx: typer.Context) -> None:
    """Check FFmpeg, download support and the background service.

    Changes nothing in DJ apps or on devices.
    """
    import shutil

    config = ctx.obj.config()
    from djlib.sources.runtimes import javascript_runtimes

    emit(
        envelope(
            {
                "workspace_id": config.workspace_id,
                "workspace": str(ctx.obj.root),
                "ffmpeg": shutil.which("ffmpeg"),
                "ffprobe": shutil.which("ffprobe"),
                "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
                "deno": shutil.which("deno"),
                "node": shutil.which("node"),
                "javascript_runtimes": javascript_runtimes(),
                "coordinator_url": client(ctx).discover(),
                "native_app_compatibility": "not_verified",
            }
        )
    )


@app.command(rich_help_panel=BUILD)
@handled
def plan(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON tracklist (see: djlib schemas)."),
) -> None:
    """Plan a collection from a JSON tracklist of local files."""
    request = CollectionRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/plans", data=request.model_dump(mode="json")))


@app.command(rich_help_panel=BUILD)
@handled
def start(
    ctx: typer.Context,
    plan_id: str,
    revision: int = typer.Option(1, "--revision"),
    key: str | None = typer.Option(None, "--key", help=KEY_HELP),
) -> None:
    """Build a planned collection. Reuse the same --key to recover an uncertain reply."""
    emit(
        client(ctx).request(
            "POST",
            "/jobs",
            data={
                "plan_id": plan_id,
                "revision": revision,
                "idempotency_key": submission_key(key, "start"),
            },
        )
    )


@app.command(rich_help_panel=LIBRARY)
@handled
def scan(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(help="Folder to index. Optional when exactly one folder is allowed."),
    ] = None,
    key: str | None = typer.Option(None, "--key", help=KEY_HELP),
) -> None:
    """Index a music folder in place.

    Files are never renamed, moved or retagged. Identity comes from tags, not audio analysis.
    """
    if path is None:
        allowed = ctx.obj.config().allowed_roots
        if len(allowed) != 1:
            raise AppError(
                "INPUT_INVALID",
                "Choose a folder to scan: "
                + (", ".join(allowed) if allowed else "no music folders are allowed yet")
                + ".",
            )
        path = Path(allowed[0])
    emit(
        client(ctx).request(
            "POST",
            "/scans",
            data={"path": local_path(path), "idempotency_key": submission_key(key, "scan")},
        )
    )


@app.command(rich_help_panel=BUILD)
@handled
def download(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON download request (see: djlib schemas)."),
) -> None:
    """Download selected public YouTube, SoundCloud or Bandcamp recordings.

    The JSON input names each recording's identity and an idempotency key.
    """
    request = DownloadRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/downloads", data=request.model_dump(mode="json")))


@app.command("source-inspect", rich_help_panel=BUILD)
@handled
def source_inspect(ctx: typer.Context, url: str) -> None:
    """Read a set's published description and chapters (this does not identify its audio)."""
    emit(client(ctx).request("POST", "/sources/inspect", data={"url": url}))


@app.command(rich_help_panel=LIBRARY)
@handled
def library(
    ctx: typer.Context,
    query: str = typer.Option("", "--query", "-q", help="Match artist, title or version."),
    limit: int = typer.Option(20, min=1, max=100),
    after: str | None = typer.Option(None, help="Cursor from the previous page."),
) -> None:
    """Browse and search indexed tracks."""
    params = {"query": query, "limit": limit}
    if after is not None:
        params["after"] = after
    emit(client(ctx).request("GET", "/library", params=params))


@app.command("collections", rich_help_panel=LIBRARY)
@handled
def collections(
    ctx: typer.Context,
    query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
    limit: int = typer.Option(20, min=1, max=100),
    after: str | None = typer.Option(None, help="Cursor from the previous page."),
):
    """List saved collections."""
    params = {"query": query, "limit": limit}
    if after is not None:
        params["after"] = after
    emit(client(ctx).request("GET", "/collections", params=params))


@app.command("reconcile", rich_help_panel=LIBRARY)
@handled
def reconcile(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON reconcile request (see: djlib schemas)."),
):
    """Update the catalog after files changed on disk (hash-pinned, explicit)."""
    from djlib.domain.reconciliation_contracts import ReconcileRequest

    request = ReconcileRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/reconciliations", data=request.model_dump(mode="json")))


@app.command(rich_help_panel=LIBRARY)
@handled
def collection(
    ctx: typer.Context,
    collection_id: str,
    after: int = typer.Option(0, min=0, help="Offset from the previous page."),
    limit: int = typer.Option(50, min=1, max=100),
) -> None:
    """Show the tracks in a collection."""
    emit(
        client(ctx).request(
            "GET", f"/collections/{collection_id}", params={"after": after, "limit": limit}
        )
    )


@app.command(rich_help_panel=DELIVER)
@handled
def export(
    ctx: typer.Context,
    collection_id: str,
    key: str | None = typer.Option(None, "--key", help=KEY_HELP),
) -> None:
    """Write a hash-checked manifest, M3U playlist and experimental rekordbox XML."""
    emit(
        client(ctx).request(
            "POST",
            "/exports",
            data={"collection_id": collection_id, "idempotency_key": submission_key(key, "export")},
        )
    )


@app.command("usb-preflight", rich_help_panel=DELIVER)
@handled
def usb_preflight(
    ctx: typer.Context,
    path: Path,
    required_bytes: int = typer.Option(0, min=0, help="Space you expect to need, in bytes."),
) -> None:
    """Check a mounted drive's free space without writing to it."""
    emit(
        client(ctx).request(
            "POST",
            "/devices/preflight",
            data={"path": local_path(path), "required_bytes": required_bytes},
        )
    )


@jobs.command("list")
@handled
def job_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, min=1, max=100),
    query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
    after: str | None = typer.Option(None, help="Cursor from the previous page."),
) -> None:
    """List recent jobs, newest first."""
    params = {"limit": limit, "query": query}
    if after is not None:
        params["after"] = after
    emit(client(ctx).request("GET", "/jobs", params=params))


@delivery.command("list")
@handled
def delivery_list(
    ctx: typer.Context,
    query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
    limit: int = typer.Option(20, min=1, max=100),
    after: str | None = typer.Option(None, help="Cursor from the previous page."),
):
    """List saved deliveries. Stored observations are not a fresh verification."""
    params = {"query": query, "limit": limit}
    if after is not None:
        params["after"] = after
    emit(client(ctx).request("GET", "/deliveries", params=params))


@delivery.command("targets")
@handled
def delivery_targets(ctx: typer.Context) -> None:
    """List supported app and player targets (these do not prove hardware playback)."""
    emit(client(ctx).request("GET", "/delivery-targets"))


@delivery.command("plan")
@handled
def delivery_plan(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON delivery request (see: djlib schemas)."),
) -> None:
    """Freeze collections for a pilot (default) or full delivery to one target."""
    body = DeliveryRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/deliveries", data=body.model_dump(mode="json")))


@delivery.command("get")
@handled
def delivery_get(ctx: typer.Context, delivery_id: str) -> None:
    """Show a delivery's stages, blockers and the next step."""
    emit(client(ctx).request("GET", f"/deliveries/{delivery_id}"))


@delivery.command("prepare")
@handled
def delivery_prepare(
    ctx: typer.Context,
    delivery_id: str,
    revision: int = typer.Option(..., help="Current delivery revision."),
    key: str | None = typer.Option(None, "--key", help=KEY_HELP),
) -> None:
    """Make separate tagged working copies and import playlists. Never writes to USB."""
    emit(
        client(ctx).request(
            "POST",
            f"/deliveries/{delivery_id}/prepare",
            data={"revision": revision, "idempotency_key": submission_key(key, "prepare")},
        )
    )


@delivery.command("bind-device")
@handled
def delivery_bind(
    ctx: typer.Context,
    delivery_id: str,
    path: Path,
    revision: int = typer.Option(..., help="Current delivery revision."),
) -> None:
    """Record the exact mounted USB volume, read-only."""
    emit(
        client(ctx).request(
            "POST",
            f"/deliveries/{delivery_id}/device",
            data={"revision": revision, "path": local_path(path)},
        )
    )


@delivery.command("observe")
@handled
def delivery_observe(
    ctx: typer.Context,
    delivery_id: str,
    file: Path | None = typer.Option(
        None, help="JSON observation (see: djlib schemas). A terminal can ask instead."
    ),
    stage: str | None = typer.Option(
        None, help="Stage to record when prompting; defaults to the delivery's next step."
    ),
) -> None:
    """Record what you actually saw in the DJ app or on the player.

    In a terminal, omit --file to answer a few questions about the frozen track list.
    """
    if file is not None:
        body = DeliveryObservation.model_validate_json(file.read_text(encoding="utf-8"))
    elif terminal.current().json:
        raise AppError("INPUT_INVALID", "Pass --file with an observation JSON.")
    else:
        from djlib.interfaces.guided import observe

        body = observe(terminal.current(), client(ctx), delivery_id, stage)
    emit(
        client(ctx).request(
            "POST", f"/deliveries/{delivery_id}/observations", data=body.model_dump(mode="json")
        )
    )


@delivery.command("verify-device")
@handled
def delivery_verify(
    ctx: typer.Context,
    delivery_id: str,
    revision: int = typer.Option(..., help="Current delivery revision."),
) -> None:
    """Re-read audio hashes from the bound USB volume."""
    emit(
        client(ctx).request(
            "POST", f"/deliveries/{delivery_id}/verify", data={"revision": revision}
        )
    )


@delivery.command("verify-app")
@handled
def delivery_verify_app(
    ctx: typer.Context,
    delivery_id: str,
    revision: int = typer.Option(..., help="Current delivery revision."),
) -> None:
    """Check working files after import and analysis. No USB or player needed."""
    emit(
        client(ctx).request(
            "POST", f"/deliveries/{delivery_id}/verify-app", data={"revision": revision}
        )
    )


@delivery.command("inspect-native-xml")
@handled
def delivery_native_xml(
    ctx: typer.Context,
    delivery_id: str,
    path: Path,
    revision: int = typer.Option(..., help="Current delivery revision."),
) -> None:
    """Compare a rekordbox Collection XML export with the prepared playlists (read-only)."""
    emit(
        client(ctx).request(
            "POST",
            f"/deliveries/{delivery_id}/native-xml",
            data={"revision": revision, "path": local_path(path)},
        )
    )


@jobs.command("get")
@handled
def job_get(ctx: typer.Context, job_id: str) -> None:
    """Show a job's state, item counts and result."""
    emit(client(ctx).request("GET", f"/jobs/{job_id}"))


@jobs.command("wait")
@handled
def job_wait(
    ctx: typer.Context,
    job_id: str,
    timeout: float = typer.Option(30, min=0, max=60, help="Seconds to wait."),
) -> None:
    """Wait briefly; a timeout leaves the job running and exits with code 3."""
    term = terminal.current()
    if term.json:
        reply = client(ctx).wait(job_id, timeout)
        emit(reply)
        result = reply["result"]
        views.exit_for_job(result, timed_out=bool(result.get("timed_out")))
        return
    _watch(ctx, job_id, timeout)


@jobs.command("watch")
@handled
def job_watch(ctx: typer.Context, job_id: str) -> None:
    """Follow a job until it finishes. Ctrl-C stops watching; the job keeps running."""
    term = terminal.current()
    if term.json:
        local = client(ctx)
        while True:
            reply = local.wait(job_id, 60)
            if not reply["result"].get("timed_out"):
                break
        emit(reply)
        views.exit_for_job(reply["result"])
        return
    _watch(ctx, job_id, None)


def _watch(ctx: typer.Context, job_id: str, timeout: float | None) -> None:
    term = terminal.current()
    local = client(ctx)
    job = local.request("GET", f"/jobs/{job_id}")["result"]
    followed = terminal.follow(term, local, job, timeout)
    views.job_card(term, followed.job, detached=followed.detached, timed_out=followed.timed_out)
    views.exit_for_job(followed.job, detached=followed.detached, timed_out=followed.timed_out)


@jobs.command("items")
@handled
def job_items(
    ctx: typer.Context,
    job_id: str,
    after: int = typer.Option(-1, help="Position from the previous page."),
    limit: int = typer.Option(20, min=1, max=100),
    state: str | None = typer.Option(None, help="succeeded, failed, skipped, needs_input…"),
) -> None:
    """List a job's items, e.g. which files failed and why."""
    params = {"after": after, "limit": limit}
    if state:
        params["state"] = state
    emit(client(ctx).request("GET", f"/jobs/{job_id}/items", params=params))


@jobs.command("events")
@handled
def job_events(ctx: typer.Context, job_id: str, after: int = typer.Option(0, min=0)) -> None:
    """Print a job's event log as JSON."""
    emit(client(ctx).request("GET", f"/jobs/{job_id}/events", params={"after": after}))


@jobs.command("control")
@handled
def job_control(
    ctx: typer.Context,
    job_id: str,
    action: Annotated[str, typer.Argument(help="pause, resume, cancel or retry")],
) -> None:
    """Pause, resume, cancel or retry a job. Retries keep items that already succeeded."""
    emit(client(ctx).request("POST", f"/jobs/{job_id}/control", data={"action": action}))


@reviews.command("list")
@handled
def review_list(
    ctx: typer.Context, job_id: str | None = typer.Option(None, help="Only this job's reviews.")
) -> None:
    """List open metadata conflicts and how to resolve each one."""
    emit(client(ctx).request("GET", "/reviews", params={"job_id": job_id} if job_id else {}))


@reviews.command("resolve")
@handled
def review_resolve(
    ctx: typer.Context,
    review_id: str,
    revision: int = typer.Option(..., help="The review's current revision."),
    choice: str = typer.Option(..., help="accept_requested, use_file_metadata or skip"),
) -> None:
    """Resolve a conflict: accept_requested, use_file_metadata or skip."""
    emit(
        client(ctx).request(
            "POST", f"/reviews/{review_id}", data={"revision": revision, "choice": choice}
        )
    )


@service.command("status")
@handled
def service_status(ctx: typer.Context) -> None:
    """Show whether the background service is running (does not start it)."""
    emit(envelope({"url": client(ctx).discover()}))


@service.command("start")
@handled
def service_start(ctx: typer.Context) -> None:
    """Start or reuse the background service, e.g. before an assistant connects over MCP."""
    local = client(ctx)
    url = local.start()
    emit(envelope({"url": url, "application_version": __version__}))


@service.command("stop")
@handled
def service_stop(ctx: typer.Context) -> None:
    """Checkpoint work and stop the background service. Accepted jobs stay saved."""
    local = client(ctx)
    if local.discover():
        emit(local.request("POST", "/shutdown"))
    else:
        emit(envelope({"state": "not_running"}))


@mcp.command("serve")
def mcp_serve(ctx: typer.Context) -> None:
    """Run MCP over stdio; stdout is reserved for protocol messages."""
    from djlib.interfaces.mcp_server import build_server

    try:
        ctx.obj.config()
        build_server(ctx.obj).run(transport="stdio")
    except (AppError, OSError, ValueError) as exc:
        # A CLI JSON error on stdout would corrupt the MCP transport before initialization.
        message = exc.message if isinstance(exc, AppError) else "Check workspace and MCP setup."
        typer.echo(message, err=True)
        raise typer.Exit(code=2) from exc


register_commands(app, client, emit, handled, panel=BUILD)


def main() -> None:
    """Console entry point; also accepts ``--json`` after the subcommand."""
    arguments = sys.argv[1:]
    boundary = arguments.index("--") if "--" in arguments else len(arguments)
    if "--json" in arguments[:boundary] and arguments[0] != "--json":
        arguments = ["--json", *[a for a in arguments[:boundary] if a != "--json"]] + arguments[
            boundary:
        ]
    try:
        app(args=arguments, prog_name="djlib")
    except KeyboardInterrupt:
        # Accepted jobs belong to the coordinator; stopping this client never cancels them.
        sys.stderr.write("\nInterrupted. Accepted jobs keep running; see 'djlib jobs list'.\n")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
