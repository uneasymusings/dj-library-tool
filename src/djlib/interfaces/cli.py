"""djlib CLI. JSON output and exit codes are the public interface for captured output.

A terminal gets readable views instead (see ``terminal.py``); ``--json`` or
``DJLIB_OUTPUT=json`` keeps the envelope in a terminal too.
"""

import importlib.util
import os
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
from djlib.interfaces import handles, terminal, views
from djlib.interfaces.client import (
    LocalClient,
    default_workspace,
    remember_workspace,
    remembered_workspace,
)
from djlib.interfaces.envelope import envelope
from djlib.interfaces.library_cli import register_commands
from djlib.interfaces.validation import validation_message
from djlib.workspace import Workspace

START = "Start here"
LIBRARY = "Your library"
DELIVER = "rekordbox and USB"
SETTINGS = "Settings"
AGENTS = "Assistants"
MORE = "More"

EPILOG = (
    "[bold]New here?[/]\n"
    "[bold]djlib init --allow-root ~/Music[/] → [bold]djlib scan[/] → "
    "[bold]djlib set tracklist.txt[/]\n\n"
    "Advanced commands (delivery, organize, plan, export…) still work and are listed in "
    "docs/ADVANCED.md. Output is JSON when piped or with [bold]--json[/]."
)

# Help lists panels in command order, so keep the journey order explicit.
ORDER = [
    *("init", "scan", "set", "status", "doctor"),
    *("library", "crates", "crate", "requests", "ui"),
    "rekordbox",
    *("use", "roots", "upgrade"),
    *("setup-agent", "mcp"),
    *("jobs", "service", "reviews"),
    # Hidden from help but still working; docs/ADVANCED.md lists them.
    *("demo", "collections", "collection", "plan", "start", "download", "source-inspect"),
    *("reconcile", "organize", "delivery", "export", "import-rekordbox", "usb-preflight"),
    *("schemas", "capabilities", "version", "completion"),
]

# Commands that render with a view registered under another name.
VIEW_NAMES = {"crates": "collections", "crate": "collection"}

# Usual install commands for a missing tool, by platform.
INSTALL = {
    "ffmpeg": {
        "darwin": "brew install ffmpeg",
        "linux": "sudo apt install ffmpeg",
        "win32": "winget install ffmpeg",
    },
    "deno": {
        "darwin": "brew install deno",
        "linux": "curl -fsSL https://deno.land/install.sh | sh",
        "win32": "winget install DenoLand.Deno",
    },
}
FIRST_STEP = "djlib init --allow-root ~/Music"


class JourneyGroup(typer.core.TyperGroup):
    def list_commands(self, ctx):
        names = super().list_commands(ctx)
        return sorted(names, key=lambda name: ORDER.index(name) if name in ORDER else len(ORDER))


def print_version(value: bool) -> None:
    if value:
        typer.echo(f"djlib {__version__}")
        raise typer.Exit()


app = typer.Typer(
    cls=JourneyGroup,
    help=(
        "[bold #f59f00]⣠⣴⣿⣦⣄ djlib[/]  From a tracklist to rekordbox and a verified USB, "
        "using the music you own."
    ),
    epilog=EPILOG,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
jobs = typer.Typer(help="Watch and control background jobs.", no_args_is_help=True)
reviews = typer.Typer(help="Resolve metadata conflicts found while indexing.", no_args_is_help=True)
service = typer.Typer(help="Start, stop or check the background service.", no_args_is_help=True)
mcp = typer.Typer(help="Serve djlib's tools to an assistant over MCP.", no_args_is_help=True)
delivery = typer.Typer(
    help="Prepare, record and verify rekordbox/Serato imports and USB delivery.",
    no_args_is_help=True,
)
roots = typer.Typer(help="Show or add the music folders djlib may read.", no_args_is_help=True)
app.add_typer(jobs, name="jobs", rich_help_panel=MORE)
app.add_typer(reviews, name="reviews", rich_help_panel=MORE)
app.add_typer(service, name="service", rich_help_panel=MORE)
app.add_typer(mcp, name="mcp", rich_help_panel=AGENTS)
app.add_typer(delivery, name="delivery", hidden=True)
app.add_typer(roots, name="roots", rich_help_panel=SETTINGS)

KEY_HELP = "Retry token for scripts: reusing it never runs the job twice."
CRATE_HELP = "Crate name, ID start (6+ characters) or last."


def install_hint(tool: str) -> str:
    """The usual install command for ``tool`` on this platform."""
    platform = sys.platform if sys.platform in {"darwin", "win32"} else "linux"
    return INSTALL[tool][platform]


def media_tool_warnings() -> list[str]:
    """Warn before indexing when MP3, FLAC, M4A and AIFF files can't be read yet."""
    import shutil

    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if not missing:
        return []
    return [
        f"{' and '.join(missing)} not found, so only WAV files can be read. "
        f"Install FFmpeg: {install_hint('ffmpeg')}"
    ]


def input_message(exc: Exception) -> str:
    """Why a named file can't be opened, without echoing what it contains."""
    if isinstance(exc, OSError) and exc.filename:
        if isinstance(exc, FileNotFoundError):
            reason = "no such file"
        elif isinstance(exc, IsADirectoryError):
            reason = "it is a folder, not a file"
        elif isinstance(exc, PermissionError):
            reason = "permission denied"
        else:
            reason = (exc.strerror or "it can't be read").lower()
        return f"Can't open {exc.filename}: {reason}."
    return "Check input JSON, paths, and the command schema."


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
                else AppError("INPUT_INVALID", input_message(exc))
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
    name = terminal.command_name()
    if name in views.FOLLOW and value.get("warnings"):
        # Following a job ends with its card and exit code, so say the warnings first.
        print_warnings(term, value["warnings"])
        value = {**value, "warnings": []}
    workspace = _workspace()
    views.show(
        term,
        VIEW_NAMES.get(name, name),
        value,
        client_factory=(lambda: LocalClient(workspace)) if workspace else None,
    )


def print_warnings(term: terminal.Terminal, warnings: list[str]) -> None:
    from rich.text import Text

    for warning in warnings:
        term.err.print(
            Text.assemble((f"{term.glyph('warn')} ", "warn"), str(warning)), soft_wrap=True
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
            "Pass --key TOKEN, a retry token you choose: reusing it never runs the job twice.",
        )
    return f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


@app.callback()
def configure(
    ctx: typer.Context,
    workspace: Path = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace folder. Default: DJLIB_WORKSPACE, else the one set by djlib use or init.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print JSON (the default when piped).",
    ),
    show_version: bool = typer.Option(
        False,
        "--version",
        is_eager=True,
        callback=print_version,
        help="Print the version and exit.",
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
        None, "--allow-root", help="A music folder djlib may read; repeatable."
    ),
) -> None:
    """Create a workspace and allow your music folders.

    djlib only reads inside these folders and never moves or retags your music.
    """
    config = ctx.obj.initialize(allow_root)
    # The first workspace becomes the default, so later commands need no --workspace.
    remembered = False
    if not os.environ.get("DJLIB_WORKSPACE") and remembered_workspace() is None:
        remember_workspace(ctx.obj.root)
        remembered = True
    reply = envelope(
        {
            "workspace": str(ctx.obj.root),
            "config": config.model_dump(mode="json"),
            "default_workspace": remembered or ctx.obj.root == Workspace(default_workspace()).root,
        }
    )
    reply["warnings"] = media_tool_warnings()
    emit(reply)


@app.command(rich_help_panel=START)
@handled
def status(ctx: typer.Context) -> None:
    """Your library at a glance: tracks, lists and crates."""
    from concurrent.futures import ThreadPoolExecutor

    from djlib.exporting.rekordbox_anlz import default_root
    from djlib.interfaces.rekordbox_cli import playlist_file_name, rekordbox_playlists, remembered

    local = client(ctx)
    # Reading rekordbox's menu takes about half a second; do it while the summary loads.
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(rekordbox_playlists)
        summary = local.request("GET", "/summary")["result"]
        playlists = reading.result()
    history = remembered(ctx.obj)
    for row in summary["recent_collections"]:
        done = history.get(row["collection_id"], {})
        # A set that changed may live in rekordbox as "Name (2)"; history knows which.
        name = done.get("playlist") or playlist_file_name(row["name"])
        row["playlist"] = name
        row["in_rekordbox"] = None if playlists is None else name in playlists
        row["pushed_at"] = done.get("pushed_at")
        row["usb"] = done.get("usb")
    summary["rekordbox_checked"] = playlists is not None
    summary["rekordbox_installed"] = default_root() is not None
    summary["service_url"] = local.discover()
    emit(envelope(summary))


@app.command(rich_help_panel=SETTINGS)
@handled
def use(
    ctx: typer.Context,
    path: Annotated[
        Path | None, typer.Argument(help="Workspace to use by default; omit to show it.")
    ] = None,
) -> None:
    """Choose the default workspace."""
    if path is not None:
        workspace = Workspace(path)
        workspace.config()  # only initialized workspaces can become the default
        remember_workspace(workspace.root)
    source = (
        "DJLIB_WORKSPACE"
        if os.environ.get("DJLIB_WORKSPACE")
        else "remembered"
        if remembered_workspace()
        else "built_in"
    )
    current = Workspace(default_workspace())
    emit(
        envelope(
            {
                "workspace": str(current.root),
                "source": source,
                "initialized": current.config_path.is_file(),
                "changed": path is not None,
            }
        )
    )


@roots.command("list")
@handled
def roots_list(ctx: typer.Context):
    """Show allowed music folders (no filesystem discovery)."""
    emit(client(ctx).request("GET", "/roots"))


@roots.command("add")
@handled
def roots_add(ctx: typer.Context, paths: Annotated[list[Path], typer.Argument()]):
    """Allow more music folders.

    Folders you already allowed stay allowed, and your music is never changed.
    """
    from djlib.domain.workspace_contracts import RootsRequest

    body = RootsRequest(paths=[local_path(path) for path in paths])
    emit(client(ctx).request("POST", "/roots", data=body.model_dump(mode="json")))


@app.command(hidden=True)
@handled
def version(ctx: typer.Context) -> None:
    """Print the version without starting the background service."""
    emit(envelope({"version": __version__}))


@app.command(hidden=True)
def completion(
    shell: Annotated[
        str | None, typer.Argument(help="bash, zsh, fish or pwsh; defaults to your shell.")
    ] = None,
    install: bool = typer.Option(False, "--install", help="Add it to your shell's startup file."),
) -> None:
    """Print the shell completion script, or install it with --install."""
    from typer._completion_shared import _get_shell_name, get_completion_script
    from typer._completion_shared import install as install_script

    shell = shell or _get_shell_name()
    if not shell:
        typer.echo("Name your shell: djlib completion zsh (or bash, fish, pwsh).", err=True)
        raise typer.Exit(2)
    if install:
        shell, path = install_script(shell=shell, prog_name="djlib", complete_var="_DJLIB_COMPLETE")
        typer.echo(f"Completion for {shell} added to {path}. Open a new terminal to use it.")
        return
    script = get_completion_script(prog_name="djlib", complete_var="_DJLIB_COMPLETE", shell=shell)
    typer.echo(script)


@app.command(rich_help_panel=AGENTS)
@handled
def setup_agent(
    ctx: typer.Context,
    output: Path = typer.Option(..., "--output", help="New folder for the session."),
) -> None:
    """Create a Claude Code or Codex session for djlib.

    The session folder gets the djlib skill, its MCP configuration and launch.py.
    """
    from djlib.application.agent_setup import create_agent_session

    emit(envelope(create_agent_session(ctx.obj, output)))


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(hidden=True)
@handled
def capabilities(ctx: typer.Context) -> None:
    """List implemented and planned capabilities."""
    emit(client(ctx).request("GET", "/capabilities"))


@app.command(rich_help_panel=START)
@handled
def doctor(ctx: typer.Context) -> None:
    """Check FFmpeg, rekordbox and the background service.

    Works before init too. Changes nothing in DJ apps or on devices. Exits 1 when FFmpeg is missing.
    """
    import shutil

    from djlib.exporting.rekordbox_anlz import default_root
    from djlib.sources.runtimes import javascript_runtimes

    initialized = ctx.obj.config_path.is_file()
    config = ctx.obj.config() if initialized else None
    rekordbox = automation = locked = None
    if sys.platform == "darwin":
        # Only macOS can drive rekordbox; elsewhere nothing here touches it.
        from djlib.native import rekordbox_mac

        rekordbox = rekordbox_mac.installed()
        automation = rekordbox_mac.automation_allowed()
        locked = rekordbox_mac.screen_locked()
    analysis = default_root()
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    runtimes = javascript_runtimes()

    fixes = {}
    if not initialized:
        fixes["workspace"] = FIRST_STEP
    if not ffmpeg:
        fixes["ffmpeg"] = install_hint("ffmpeg")
    if not ffprobe:
        fixes["ffprobe"] = install_hint("ffmpeg")  # ffprobe ships with FFmpeg
    if not runtimes.get("youtube_runtime_ready"):
        fixes["javascript_runtime"] = install_hint("deno")
    if locked:
        fixes["screen"] = "Unlock your Mac; rekordbox can't be driven while it is locked."
    required = bool(ffmpeg and ffprobe)
    reply = envelope(
        {
            "workspace_id": config.workspace_id if config else None,
            "workspace": str(ctx.obj.root),
            "workspace_initialized": initialized,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
            "deno": shutil.which("deno"),
            "node": shutil.which("node"),
            "javascript_runtimes": runtimes,
            "coordinator_url": client(ctx).discover() if initialized else None,
            "rekordbox": rekordbox,
            "rekordbox_automation_allowed": automation,
            # A locked Mac blocks every rekordbox step; worth seeing before a long wait.
            "screen_locked": locked,
            "rekordbox_analysis_folder": str(analysis) if analysis else None,
            "native_app_compatibility": "not_verified",
            "required_checks_passed": required,
            "fixes": fixes,
        }
    )
    # The doctor view shows each failed check with its fix; JSON readers get `fixes`.
    emit(reply)
    if not required:
        raise typer.Exit(1)


@app.command(rich_help_panel=LIBRARY)
@handled
def ui(
    ctx: typer.Context,
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the page in your browser (terminal only)."
    ),
) -> None:
    """Open the review page: library, requests, crates, deliveries.

    The link includes this workspace's local access token; keep it to yourself.
    """
    from urllib.parse import urlencode

    url = client(ctx).start()
    term = terminal.current()
    handoff = {"token": ctx.obj.token()}
    if term.workspace is not None:
        handoff["ws"] = terminal.shell_path(term.workspace)
    page = f"{url}/ui/#{urlencode(handoff)}"
    if not term.json and open_browser:
        import webbrowser

        webbrowser.open(page)
    emit(envelope({"url": page}))


@app.command(hidden=True)
@handled
def plan(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON tracklist (see: djlib schemas)."),
) -> None:
    """Plan a collection from a JSON tracklist of local files."""
    request = CollectionRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/plans", data=request.model_dump(mode="json")))


@app.command(hidden=True)
@handled
def start(
    ctx: typer.Context,
    plan_id: str,
    revision: int = typer.Option(1, "--revision"),
    key: str | None = typer.Option(None, "--key", help=KEY_HELP, hidden=True),
) -> None:
    """Build a planned collection."""
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


def folder_key(key: str, folder: Path) -> str:
    """One retry token per music folder, so an unplugged drive never shifts the others'."""
    import hashlib

    digest = hashlib.sha256(local_path(folder).encode()).hexdigest()[:16]
    return f"{key[:180]}:{digest}"


@app.command(rich_help_panel=START)
@handled
def scan(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(help="Folder to index; defaults to all your music folders."),
    ] = None,
    key: str | None = typer.Option(None, "--key", help=KEY_HELP, hidden=True),
) -> None:
    """Index your music so djlib knows which songs you own.

    Files are never renamed, moved or retagged; songs are known by their tags.
    """
    term = terminal.current()
    warnings = media_tool_warnings()
    if path is not None:
        folders, allowed = [path], [path]
    else:
        allowed = ctx.obj.config().allowed_roots
        if not allowed:
            raise AppError(
                "INPUT_INVALID", "No music folders are allowed yet. Add one: djlib roots add PATH"
            )
        # An unplugged drive should not stop the other folders from being indexed.
        folders = [Path(root) for root in allowed if Path(root).is_dir()]
        warnings += [
            f"Skipped {root}: it isn't there (unplugged drive?)."
            for root in allowed
            if not Path(root).is_dir()
        ]
        if not folders:
            raise AppError(
                "FILE_UNAVAILABLE",
                "None of your music folders can be found. Plug the drive back in, "
                "or add another folder: djlib roots add PATH",
            )
    key = submission_key(key, "scan")
    local = client(ctx)
    replies = [
        local.request(
            "POST",
            "/scans",
            data={
                "path": local_path(folder),
                # A single folder keeps the token as given, as before several were scanned.
                "idempotency_key": key if len(allowed) == 1 else folder_key(key, folder),
            },
        )
        for folder in folders
    ]
    if len(replies) > 1 and term.json:
        replies = [envelope({"scans": [scanned["result"] for scanned in replies]})]
    replies[0]["warnings"] = [*replies[0].get("warnings", []), *warnings]
    if len(replies) == 1:
        emit(replies[0])
        return
    # In a terminal, follow each folder's scan in turn and exit with the worst outcome.
    code = 0
    for reply in replies:
        try:
            emit(reply)
        except typer.Exit as exited:
            if exited.exit_code == 130:
                raise
            code = max(code, exited.exit_code)
    if code:
        raise typer.Exit(code)


@app.command(hidden=True)
@handled
def download(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON download request (see: djlib schemas)."),
) -> None:
    """Download selected public YouTube, SoundCloud or Bandcamp recordings.

    The JSON file names each recording and a retry token (idempotency_key).
    """
    request = DownloadRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/downloads", data=request.model_dump(mode="json")))


@app.command("source-inspect", hidden=True)
@handled
def source_inspect(
    ctx: typer.Context,
    url: str,
    comments: int = typer.Option(
        0, "--comments", min=0, max=2000, help="Also read up to this many listener comments."
    ),
) -> None:
    """Read a set's description and chapters, and optionally its comments."""
    emit(client(ctx).request("POST", "/sources/inspect", data={"url": url, "comments": comments}))


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


@handled
def collections(
    ctx: typer.Context,
    query: str = typer.Option("", "--query", "-q", help="Match a name or ID."),
    limit: int = typer.Option(20, min=1, max=100),
    after: str | None = typer.Option(None, help="Cursor from the previous page."),
):
    """List your crates (saved collections), newest first."""
    from djlib.interfaces.rekordbox_cli import remembered

    params = {"query": query, "limit": limit}
    if after is not None:
        params["after"] = after
    reply = client(ctx).request("GET", "/collections", params=params)
    # The same rekordbox/USB record `status` shows: what djlib pushed and last checked.
    history = remembered(ctx.obj)
    for row in reply["result"].get("collections") or []:
        done = history.get(row.get("collection_id"), {})
        row["rekordbox"] = {
            "playlist": done.get("playlist"),
            "pushed_at": done.get("pushed_at"),
            "usb": done.get("usb"),
        }
    emit(reply)


app.command("crates", rich_help_panel=LIBRARY)(collections)
app.command("collections", hidden=True)(collections)


@app.command("reconcile", hidden=True)
@handled
def reconcile(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", help="JSON reconcile request (see: djlib schemas)."),
):
    """Update the catalog after files moved or changed on disk; the file lists each change."""
    from djlib.domain.reconciliation_contracts import ReconcileRequest

    request = ReconcileRequest.model_validate_json(file.read_text(encoding="utf-8"))
    emit(client(ctx).request("POST", "/reconciliations", data=request.model_dump(mode="json")))


@handled
def collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(metavar="CRATE", help=CRATE_HELP)],
    after: int = typer.Option(0, min=0, help="Offset from the previous page."),
    limit: int = typer.Option(50, min=1, max=100),
) -> None:
    """Show the tracks in a crate."""
    local = client(ctx)
    collection_id = handles.resolve(local, "collection", collection_id)
    emit(
        local.request(
            "GET", f"/collections/{collection_id}", params={"after": after, "limit": limit}
        )
    )


app.command("crate", rich_help_panel=LIBRARY)(collection)
app.command("collection", hidden=True)(collection)


@app.command(hidden=True)
@handled
def export(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(metavar="CRATE", help=CRATE_HELP)],
    key: str | None = typer.Option(None, "--key", help=KEY_HELP, hidden=True),
) -> None:
    """Write a hash-checked manifest, M3U playlist and experimental rekordbox XML."""
    key = submission_key(key, "export")
    local = client(ctx)
    collection_id = handles.resolve(local, "collection", collection_id)
    emit(
        local.request(
            "POST", "/exports", data={"collection_id": collection_id, "idempotency_key": key}
        )
    )


@app.command("import-rekordbox", hidden=True)
@handled
def import_rekordbox(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="rekordbox File > Export Collection in xml format.")],
) -> None:
    """Bring rekordbox's BPM and key analysis into your catalog.

    Tracks match by exact file location. Values you set yourself are never overwritten.
    """
    import shutil

    ctx.obj.config()
    source = path.expanduser().absolute()
    if not source.is_file():
        raise AppError("FILE_REQUIRED", "Choose the exported rekordbox XML file.")
    if source.stat().st_size > 32 * 1024 * 1024:
        raise AppError("NATIVE_XML_LIMIT", "Native XML is limited to 32 MiB.")
    # The coordinator only reads allowed folders; this explicit choice is copied into the
    # workspace instead of widening that permission.
    target = ctx.obj.incoming / "rekordbox" / f"collection-{datetime.now():%Y%m%d-%H%M%S}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    emit(client(ctx).request("POST", "/analysis/rekordbox", data={"path": str(target)}))


@app.command("usb-preflight", hidden=True)
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
    file: Path | None = typer.Option(None, "--file", help="JSON delivery request."),
    collection: list[str] = typer.Option(
        None, "--collection", help="Crate to deliver (name, ID or last); repeat for several."
    ),
    workflow: str | None = typer.Option(
        None, help="rekordbox_import, serato_import, rekordbox_usb or serato_portable."
    ),
    app_version: str | None = typer.Option(None, help="Version shown in the app's About box."),
    name: str | None = typer.Option(None, help="Delivery name; defaults to the collection's."),
    player: str | None = typer.Option(None, help="Player profile for rekordbox_usb."),
    full: bool = typer.Option(False, "--full", help="Deliver everything, not a small pilot."),
) -> None:
    """Freeze collections for a pilot (default) or full delivery to one target.

    Use flags (--collection CRATE --workflow rekordbox_import --app-version 7.2.3) or --file.
    """
    if file is not None:
        body = DeliveryRequest.model_validate_json(file.read_text(encoding="utf-8"))
    elif collection and workflow and app_version:
        local = client(ctx)
        collection = [handles.resolve(local, "collection", value) for value in collection]
        if name is None:
            first = local.request("GET", f"/collections/{collection[0]}", params={"limit": 1})
            name = f"{first['result']['name']} ({workflow.replace('_', ' ')})"
        body = DeliveryRequest(
            name=name,
            collection_ids=collection,
            workflow=workflow,
            app_version=app_version,
            hardware_profile=player,
            phase="full" if full else "pilot",
        )
    else:
        raise AppError(
            "INPUT_INVALID",
            "Pass --file, or --collection, --workflow and --app-version.",
        )
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
    key: str | None = typer.Option(None, "--key", help=KEY_HELP, hidden=True),
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
    """Wait briefly for a job to finish.

    A timeout leaves the job running and exits with code 3.
    """
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
    """Follow a job until it finishes.

    Ctrl-C stops watching; the job keeps running.
    """
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
    state: str | None = typer.Option(None, help="Only this state, e.g. failed."),
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
    """Pause, resume, cancel or retry a job.

    A retry keeps the items that already succeeded.
    """
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
    """Start the background service, or reuse the running one.

    Useful before an assistant connects over MCP.
    """
    local = client(ctx)
    url = local.start()
    emit(envelope({"url": url, "application_version": __version__}))


@service.command("stop")
@handled
def service_stop(ctx: typer.Context) -> None:
    """Stop the background service.

    Running work is checkpointed first; accepted jobs stay saved.
    """
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
        # No config() check: without a workspace the server still starts and every tool
        # answers WORKSPACE_REQUIRED with the `djlib init` command, so a plugin installed
        # before setup isn't a dead server.
        build_server(ctx.obj).run(transport="stdio")
    except (AppError, OSError, ValueError) as exc:
        # A CLI JSON error on stdout would corrupt the MCP transport before initialization.
        message = exc.message if isinstance(exc, AppError) else "Check workspace and MCP setup."
        typer.echo(message, err=True)
        raise typer.Exit(code=2) from exc


register_commands(app, client, emit, handled, panel=LIBRARY)

from djlib.interfaces.rekordbox_cli import register_rekordbox  # noqa: E402

register_rekordbox(app, client, emit, handled, panel=DELIVER, start_panel=START)

from djlib.interfaces.upgrade_cli import register_upgrade  # noqa: E402

register_upgrade(app, client, emit, handled, panel=SETTINGS)


def hoisted(arguments: list[str]) -> list[str]:
    """Move ``--json`` and ``-w/--workspace PATH`` given after the subcommand to the front.

    They belong to ``djlib`` itself, so ``djlib scan -w ~/lib`` means ``djlib -w ~/lib scan``.
    Nothing after ``--`` moves.
    """
    boundary = arguments.index("--") if "--" in arguments else len(arguments)
    head, front, rest = arguments[:boundary], [], []
    index = 0
    while index < len(head):
        argument = head[index]
        if argument == "--json":
            if argument not in front:
                front.append(argument)
        elif argument in {"-w", "--workspace"} and index + 1 < len(head):
            front += [argument, head[index + 1]]
            index += 1
        elif argument.startswith("--workspace="):
            front.append(argument)
        else:
            rest.append(argument)
        index += 1
    return [*front, *rest, *arguments[boundary:]]


def main() -> None:
    """Console entry point; also accepts ``--json`` and ``-w PATH`` after the subcommand."""
    arguments = hoisted(sys.argv[1:])
    try:
        app(args=arguments, prog_name="djlib")
    except KeyboardInterrupt:
        # Accepted jobs belong to the coordinator; stopping this client never cancels them.
        sys.stderr.write("\nInterrupted. Accepted jobs keep running; see 'djlib jobs list'.\n")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
