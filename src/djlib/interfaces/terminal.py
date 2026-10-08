"""Human terminal presentation; captured output keeps the JSON contract unchanged.

Output is the JSON envelope whenever stdout is not a terminal (assistants, pipes, CI), with
``--json``, or with ``DJLIB_OUTPUT=json``. A terminal gets readable views, live job progress on
stderr and copy-pasteable next steps. Presentation never changes what a command submits.
"""

import json
import os
import shlex
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.padding import Padding
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

OUTPUT_ENV = "DJLIB_OUTPUT"
META_KEY = "djlib.terminal"

# Named ANSI colors follow the user's terminal theme on light and dark backgrounds;
# only the brand amber is fixed, and Rich downgrades it on 256-color terminals.
THEME = Theme(
    {
        "brand": "bold #f59f00",
        "accent": "#f59f00",
        "path": "cyan",
        "ok": "green",
        "warn": "yellow",
        "bad": "red",
        "info": "blue",
        "muted": "dim",
        "label": "dim",
        "cmd": "bold",
        "heading": "bold",
    }
)

# Rich's nearest 16-color match for the amber is bright red, which reads as an error.
LOW_COLOR = Theme({"brand": "bold yellow", "accent": "yellow"})


def fit_colors(console: Console) -> Console:
    """Use the terminal's own yellow for the accent where only 16 colors exist."""
    if console.color_system in {"standard", "windows"}:
        console.push_theme(LOW_COLOR)
    return console


_GLYPHS = {
    "ok": ("✓", "+"),
    "bad": ("✗", "x"),
    "warn": ("!", "!"),
    "todo": ("○", "o"),
    "run": ("◐", "*"),
    "pause": ("‖", "="),
    "skip": ("–", "-"),
    "arrow": ("→", ">"),
    "dot": ("·", "|"),
    "mark": ("⣠⣴⣿⣦⣄", "::"),
    "bar_full": ("█", "#"),
    "bar_empty": ("░", "."),
}

ACTIVE_STATES = frozenset({"queued", "running"})
DONE_ITEM_STATES = frozenset({"succeeded", "failed", "skipped", "cancelled"})

JOB_LABELS = {
    "scan": "Indexing music",
    "collection": "Building collection",
    "download": "Downloading recordings",
    "export": "Writing export files",
    "delivery": "Preparing working copies",
    "delivery_check": "Checking delivery files",
    "organize": "Organizing collection",
    "organization": "Organizing collection",
    "reconcile": "Reconciling changed files",
}


def wants_json(flag: bool) -> bool:
    if flag:
        return True
    choice = os.environ.get(OUTPUT_ENV, "").strip().lower()
    if choice == "json":
        return True
    if choice in {"pretty", "human", "text"}:
        return False
    return not sys.stdout.isatty()


@dataclass
class Terminal:
    """Per-invocation presentation state stored in the Click context."""

    json: bool
    workspace: Path | None = None
    out: Console = field(init=False, repr=False)
    err: Console = field(init=False, repr=False)
    # Envelope warnings, printed under the first headline of the view.
    pending: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.out = fit_colors(Console(theme=THEME, highlight=False))
        self.err = fit_colors(Console(theme=THEME, highlight=False, stderr=True))

    @property
    def unicode(self) -> bool:
        return (self.out.encoding or "").lower().replace("-", "").startswith("utf")

    def glyph(self, name: str) -> str:
        fancy, plain = _GLYPHS[name]
        return fancy if self.unicode else plain

    def command(self, *parts: str | Path) -> str:
        """A copy-pasteable command, including the workspace when it is not the default.

        ``Path`` parts are written as ``~/…`` when that stays unquoted.
        """
        words = ["djlib"]
        if self.workspace is not None:
            words += ["--workspace", shell_path(self.workspace)]
        return " ".join(
            words
            + [shell_path(part) if isinstance(part, Path) else quote(str(part)) for part in parts]
        )

    def print_json(self, value: dict) -> None:
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


# Typer may vendor its own Click, so commands bind their context explicitly.
_ACTIVE: ContextVar = ContextVar("djlib_cli_context", default=None)


def bind(ctx):
    return _ACTIVE.set(ctx)


def unbind(token) -> None:
    _ACTIVE.reset(token)


def active_context():
    return _ACTIVE.get()


def params() -> dict:
    ctx = active_context()
    return dict(ctx.params) if ctx is not None else {}


def current() -> Terminal:
    """The active terminal; JSON whenever presentation was not configured."""
    ctx = active_context()
    term = ctx.find_root().meta.get(META_KEY) if ctx is not None else None
    return term if isinstance(term, Terminal) else Terminal(json=True)


def command_parts() -> list[str]:
    """Subcommand path without the program name, e.g. ``["jobs", "list"]``."""
    ctx, names = active_context(), []
    while ctx is not None and ctx.parent is not None:
        names.append(ctx.info_name or "")
        ctx = ctx.parent
    return list(reversed(names))


def command_name() -> str:
    return " ".join(command_parts())


# -- formatting helpers ------------------------------------------------------------------------


def quote(value: str) -> str:
    if os.name == "nt":
        return f'"{value}"' if any(c in value for c in ' \t&|<>^"') else value
    return shlex.quote(value)


def shell_path(path: Path | str) -> str:
    """Prefer ``~/…`` when it stays unquoted, so tilde expansion still applies."""
    path = Path(path)
    if os.name != "nt":
        try:
            relative = path.relative_to(Path.home())
        except ValueError:
            relative = None
        if relative is not None and quote(str(relative)) == str(relative):
            return "~" if str(relative) == "." else f"~/{relative}"
    return quote(str(path))


def short_path(value: str | None) -> str:
    if not value:
        return ""
    home = str(Path.home())
    if os.name != "nt" and (value == home or value.startswith(home + os.sep)):
        return "~" + value[len(home) :]
    return value


def path_text(value: str | None) -> Text:
    return Text(short_path(value), style="path") if value else Text("—", style="muted")


def short_id(value: str | None) -> str:
    """``b3dbc172`` for ``collection_b3dbc172…``; commands accept unique prefixes."""
    prefix, _, rest = str(value or "").rpartition("_")
    return rest[:8] if prefix and rest else str(value or "")


def indented(renderable, indent: int = 2) -> Padding:
    """Shift right; wrapped lines keep the indent (and no full-width trailing spaces)."""
    return Padding(renderable, (0, 0, 0, indent), expand=False)


def hanging(console: Console, lead: Text, body: Text, indent: int = 0) -> None:
    """``lead body`` where a long body wraps under its own first column, not column 0."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row(lead, body)
    console.print(indented(grid, indent) if indent else grid)


WORDS = {
    "bpm": "BPM",
    "cli": "CLI",
    "id": "ID",
    "ids": "IDs",
    "json": "JSON",
    "mcp": "MCP",
    "m3u": "M3U",
    "rekordbox": "rekordbox",
    "serato": "Serato",
    "soulseek": "Soulseek",
    "url": "URL",
    "usb": "USB",
    "xml": "XML",
}


def humanize(key: str) -> str:
    words = key.replace("_", " ").strip().split(" ")
    words = [WORDS.get(word.lower(), word) for word in words]
    text = " ".join(words)
    return text[:1].upper() + text[1:] if words and words[0] not in WORDS.values() else text


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def ago(value: str | None) -> str:
    moment = parse_time(value)
    if moment is None:
        return ""
    seconds = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if seconds < 45:
        return "just now"
    for size, unit in ((86_400 * 7, "w"), (86_400, "d"), (3_600, "h"), (60, "m")):
        if seconds >= size:
            amount = seconds // size
            if unit == "w" and amount > 8:
                return moment.astimezone().strftime("%Y-%m-%d")
            return f"{amount}{unit} ago"
    return "just now"


def duration(seconds) -> str:
    if not isinstance(seconds, int | float) or seconds < 0:
        return ""
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def size(value) -> str:
    if not isinstance(value, int | float):
        return ""
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1000 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1000
    return ""


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else many or word + 's'}"


# -- errors ------------------------------------------------------------------------------------

# Plain titles for the codes people meet most; others are humanized from the code.
ERROR_TITLES = {
    "WORKSPACE_REQUIRED": "djlib isn't set up yet",
    "WORKSPACE_NOT_EMPTY": "That folder isn't empty",
    "INPUT_INVALID": "That input didn't work",
    "NOT_FOUND": "Not found",
    "TRANSPORT_UNCERTAIN": "Lost contact with djlib's background service",
    "SERVICE_UNREACHABLE": "Can't reach djlib's background service",
    "SERVICE_START_BUSY": "djlib's background service is still starting",
    "SERVICE_START_FAILED": "djlib's background service didn't start",
    "SERVICE_START_TIMEOUT": "djlib's background service didn't start",
    "COORDINATOR_START_REQUIRED": "djlib's background service isn't running",
    "COORDINATOR_VERSION_MISMATCH": "djlib was updated",
    "CLIENT_OUTDATED": "A newer djlib is running",
    "AUTH_REQUIRED": "djlib's background service didn't accept this request",
    "TOKEN_MISSING": "djlib's access token is missing",
    "APP_SELECTION_TIMEOUT": "You didn't pick the playlist in time",
    "APP_AUTOMATION_NOT_ALLOWED": "macOS hasn't allowed djlib to control apps yet",
    "APP_AUTOMATION_UNSUPPORTED": "Driving rekordbox needs a Mac",
    "APP_AUTOMATION_FAILED": "rekordbox didn't respond",
    "APP_SCREEN_LOCKED": "Your Mac is locked",
    "APP_NOT_INSTALLED": "rekordbox isn't installed",
    "APP_NOT_READY": "rekordbox didn't finish starting",
    "APP_IDLE_TIMEOUT": "Your Mac never went idle",
    "APP_EXPORT_TIMEOUT": "rekordbox took too long to export",
    "APP_IMPORT_NOT_FOUND": "The playlist didn't show up in rekordbox",
    "APP_PLAYLIST_NAME_TAKEN": "rekordbox already has a different playlist with that name",
    "APP_MENU_DISABLED": "rekordbox's menu isn't available right now",
    "APP_MENU_MISSING": "rekordbox's menu looks different",
    "APP_DIALOG_MISSING": "rekordbox didn't open its dialog",
    "APP_DIALOG_FAILED": "rekordbox's dialog didn't finish",
    "DEVICE_REQUIRED": "Which USB?",
    "DEVICE_UNAVAILABLE": "That USB isn't available",
    "DEVICE_CHANGED": "The USB changed",
    "SOURCE_BROWSER_ONLY": "Open this one in your browser",
    "SOURCE_UNSUPPORTED": "That link isn't supported",
    "SOURCE_NOT_ALLOWED": "djlib isn't allowed to read that folder",
    "SOURCE_ROOT_INVALID": "That folder doesn't work",
    "SOURCE_TIMEOUT": "The site took too long",
    "SOURCE_RATE_LIMITED": "The site is limiting requests",
    "SOURCE_AUTH_REQUIRED": "That page needs a login",
    "SOURCE_UNAVAILABLE": "That recording isn't available",
    "SOURCE_FAILED": "Couldn't read that page",
    "TRACKLIST_NOT_FOUND": "No tracklist on that page",
    "DEPENDENCY_REQUIRED": "Something needs installing",
    "JAVASCRIPT_RUNTIME_REQUIRED": "Something needs installing",
    "COLLECTION_EMPTY": "Nothing to put in the crate",
    "ITEM_LIMIT": "Too many files at once",
    "SCAN_SCOPE": "Scan a music folder instead",
    "DISK_SPACE": "Not enough disk space",
    "DISK_RESERVE_REACHED": "Not enough disk space",
    "FILE_CHANGED": "A file changed",
    "FILE_UNAVAILABLE": "A file is missing",
    "FORMAT_UNSUPPORTED": "That file format isn't supported",
    "IDEMPOTENCY_CONFLICT": "That --key was already used",
    "REQUEST_STALE": "The list changed since you last looked",
    "DELIVERY_STALE": "The delivery changed since you last looked",
    "REVIEW_STALE": "The review changed since you last looked",
    "PLAN_STALE": "The plan is out of date",
}
ACCESSIBILITY_SETTINGS = (
    'open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"'
)
# (label, command): a tuple is a djlib command, a string a plain shell command.
ERROR_HINTS: dict[str, tuple[str, tuple | str | None]] = {
    "WORKSPACE_REQUIRED": ("Create a workspace first", ("init", "--allow-root", "PATH")),
    "WORKSPACE_NOT_EMPTY": ("Pick a new, empty folder for --workspace", None),
    "ROOTS_NOT_UPDATED": ("Add folders to an existing workspace", ("roots", "add", "PATH")),
    "SOURCE_NOT_ALLOWED": ("Allow the folder first", ("roots", "add", "PATH")),
    "SOURCE_ROOT_INVALID": ("Check that the folder exists and is a directory", None),
    "COORDINATOR_VERSION_MISMATCH": ("When it's done, stop the old service", ("service", "stop")),
    "CLIENT_OUTDATED": ("Install the newer djlib", ("upgrade",)),
    "SERVICE_START_BUSY": ("Check the background service", ("service", "status")),
    "SERVICE_START_TIMEOUT": ("Check the background service", ("service", "status")),
    "SERVICE_START_FAILED": ("Check your setup", ("doctor",)),
    "TOKEN_MISSING": ("Check your setup", ("doctor",)),
    "DELIVERY_STALE": ("Read the current revision, then retry with it", None),
    "REVIEW_STALE": ("List open reviews for their current revision", ("reviews", "list")),
    "REQUEST_STALE": ("Read the current revision, then retry with it", None),
    "PLAN_STALE": ("Plan the collection again", None),
    "APP_PLAYLIST_NAME_TAKEN": (
        "Rename the old playlist in rekordbox, or rerun djlib set with --name",
        None,
    ),
    "TRANSPORT_UNCERTAIN": ("Retry with the same --key; it will not run twice", None),
    "IDEMPOTENCY_CONFLICT": ("That --key was used for different input; choose a new key", None),
    "DEPENDENCY_REQUIRED": ("See what is missing", ("doctor",)),
    "JAVASCRIPT_RUNTIME_REQUIRED": ("See what is missing", ("doctor",)),
    "APP_AUTOMATION_NOT_ALLOWED": (
        "Allow your terminal app under Accessibility, then retry",
        ACCESSIBILITY_SETTINGS,
    ),
    "APP_SCREEN_LOCKED": ("Unlock your Mac, then run the same command again", None),
    "APP_SELECTION_TIMEOUT": (
        "Run it again, then click the playlist in rekordbox when asked",
        None,
    ),
    "DEVICE_REQUIRED": ("Name the stick, e.g. --device /Volumes/NAME", None),
    "SOURCE_BROWSER_ONLY": ("Paste the tracklist into a text file, then", ("set", "FILE")),
}


def ffmpeg_install() -> str | None:
    """The one-line FFmpeg install where it is unambiguous (Homebrew on macOS)."""
    return "brew install ffmpeg" if sys.platform == "darwin" else None


def needs_ffmpeg(error: dict) -> bool:
    message = str(error.get("message") or "").lower()
    return error.get("code") == "DEPENDENCY_REQUIRED" and (
        "ffmpeg" in message or "ffprobe" in message
    )


def render_error(term: Terminal, error: dict) -> None:
    code = str(error.get("code") or "REQUEST_FAILED")
    message = str(error.get("message") or "The request failed.")
    title = Text.assemble((ERROR_TITLES.get(code) or humanize(code.lower()), "bold bad"))
    title.append(f"  ({code})", style="muted")
    hanging(term.err, Text(term.glyph("bad"), style="bad"), title)
    term.err.print(indented(Text(message)))
    hint = ERROR_HINTS.get(code)
    if needs_ffmpeg(error) and ffmpeg_install():
        hint = ("Install FFmpeg, then retry", ffmpeg_install())
    if hint:
        label, parts = hint
        line = Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), (label, ""))
        if parts:
            command = parts if isinstance(parts, str) else term.command(*parts)
            line.append("  " if len(label) + len(command) + 8 <= term.err.width else "\n    ")
            line.append(command, style="cmd")
        term.err.print(line, soft_wrap=True)
    elif error.get("retryable"):
        term.err.print(
            Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), "This is safe to retry.")
        )


# -- live job progress -------------------------------------------------------------------------


class CountColumn(ProgressColumn):
    """``12/40`` once item totals are known; blank while files are being discovered."""

    def render(self, task):
        if task.total is None:
            return Text("")
        return Text(f"{int(task.completed)}/{int(task.total)}", style="")


@dataclass
class Followed:
    job: dict
    timed_out: bool = False
    detached: bool = False


def job_progress(counts: dict) -> tuple[int, int]:
    total = sum(int(v) for v in counts.values())
    done = sum(int(counts.get(state, 0)) for state in DONE_ITEM_STATES)
    return done, total


def progress_bar(term: Terminal) -> Progress:
    return Progress(
        SpinnerColumn(style="accent", finished_text=term.glyph("ok")),
        TextColumn("[bold]{task.description}"),
        BarColumn(
            bar_width=28,
            style="muted",
            complete_style="accent",
            finished_style="ok",
            pulse_style="accent",
        ),
        CountColumn(),
        TextColumn("{task.fields[detail]}", style="muted"),
        TimeElapsedColumn(),
        console=term.err,
        transient=True,
    )


def follow(term: Terminal, client, job: dict, timeout: float | None = None) -> Followed:
    """Show live progress until the job leaves queued/running; Ctrl-C only detaches."""
    if job.get("state") not in ACTIVE_STATES:
        return Followed(job)
    label = JOB_LABELS.get(job.get("kind", ""), humanize(job.get("kind") or "job"))
    deadline = None if timeout is None else time.monotonic() + timeout
    progress = progress_bar(term)
    try:
        with progress:
            task = progress.add_task(label, total=None, detail="")
            while True:
                done, total = job_progress(job.get("counts") or {})
                failed = int((job.get("counts") or {}).get("failed", 0))
                detail = "ctrl-c to detach"
                if job.get("state") == "queued" and not total:
                    detail = "queued · " + detail if term.unicode else "queued, " + detail
                if failed:
                    detail = f"{failed} failed"
                progress.update(task, total=total or None, completed=done, detail=detail)
                if job.get("state") not in ACTIVE_STATES:
                    return Followed(job)
                if deadline is not None and time.monotonic() >= deadline:
                    return Followed(job, timed_out=True)
                time.sleep(0.25)
                job = client.request("GET", f"/jobs/{job['job_id']}")["result"]
    except KeyboardInterrupt:
        return Followed(job, detached=True)
