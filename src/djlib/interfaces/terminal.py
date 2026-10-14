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
from rich import control
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

# Text from set pages, comments, MixesDB and Shazam exports is shown here. Rich drops only a
# few control codes from text; drop every one but newline and tab (an escape sequence could
# clear the screen or forge a link), and the marks that reorder or hide text.
UNSAFE_CODEPOINTS = (
    *range(0x00, 0x09),
    *range(0x0B, 0x20),
    *range(0x7F, 0xA0),
    0x200B,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
)
control._CONTROL_STRIP_TRANSLATE.update(dict.fromkeys(UNSAFE_CODEPOINTS))

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
    "other": ("≈", "~"),  # you own another mix of the song
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
    "identify": "Identifying a set",
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
    "SINGLE_SOURCE_REQUIRED": "Use the link of one set",
    "SET_PREVIEW_ONLY": "Only a preview of that set plays",
    "RECOGNITION_RATE_LIMITED": "Song recognition is turning requests away",
    "RECOGNITION_UNAVAILABLE": "Song recognition can't be reached",
    "RECOGNIZER_NOT_CONFIGURED": "Song recognition isn't set up",
    "RECOGNIZER_REJECTED": "The recognition service refused djlib's key",
    "JOB_CANCELLED": "Stopped",
    "JOB_PAUSED": "Paused",
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
    "SHAZAM_HISTORY_NOT_FOUND": "No Shazam history found",
    "SHAZAM_ACCESS_DENIED": "djlib can't open your Shazam history yet",
    "SHAZAM_HISTORY_UNREADABLE": "That Shazam library can't be read",
    "SHAZAM_BACKUP_ENCRYPTED": "That iPhone backup is encrypted",
    "SHAZAM_NOTHING_NEW": "Nothing new Shazamed",
    "SHAZAM_MUSIC_NOT_ALLOWED": "macOS hasn't allowed djlib to control Music yet",
    "SHAZAM_MUSIC_UNSUPPORTED": "Reading Apple Music needs a Mac",
    "SHAZAM_MUSIC_FAILED": "Music didn't respond",
    "SHAZAM_PLAYLIST_NOT_FOUND": "Music has no “My Shazam Tracks” playlist",
}
ACCESSIBILITY_SETTINGS = (
    'open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"'
)
# (label, command): a tuple is a djlib command, a string a plain shell command.
ERROR_HINTS: dict[str, tuple[str, tuple | str | None]] = {
    # The command comes from cli.FIRST_STEP (first_step), as doctor gives it.
    "WORKSPACE_REQUIRED": ("Create a workspace first", None),
    "WORKSPACE_NOT_EMPTY": ("Pick a new, empty folder for --workspace", None),
    "ROOTS_NOT_UPDATED": ("Add folders to an existing workspace", ("roots", "add", "PATH")),
    # The folder comes from the error's details.path.
    "SOURCE_NOT_ALLOWED": ("Allow the folder with djlib roots add", None),
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
    "SOURCE_BROWSER_ONLY": ("Copy the tracklist on the page, then", ("set", "paste")),
    "SET_PREVIEW_ONLY": ("Paste the set's tracklist into a text file, then", None),
    "TRACKLIST_NOT_FOUND": ("Copy the set's tracklist (e.g. from 1001Tracklists), then", None),
    "RECOGNITION_RATE_LIMITED": ("Run the same command in an hour or so; it continues", None),
    "RECOGNITION_UNAVAILABLE": ("Check the connection, then run the same command again", None),
    "RECOGNITION_FAILING": ("Run the same command later; it continues", None),
    "RECOGNIZER_REJECTED": ("Try again later; or see other services", ("doctor",)),
    "DISK_FULL": ("Free some space, then run the same command again; it continues", None),
    "RECOGNIZER_NOT_CONFIGURED": (
        "Restart the background service so it reads your settings",
        ("service", "stop"),
    ),
    "SHAZAM_HISTORY_NOT_FOUND": ("See how to turn on Shazam sync", ("shazam", "status")),
}


def first_step() -> tuple:
    """The parts of ``djlib init …`` for a first workspace (cli.FIRST_STEP), ``~/…`` kept as a
    path so it is printed unquoted and still expands."""
    from djlib.interfaces.cli import FIRST_STEP

    words = shlex.split(FIRST_STEP)[1:]
    return tuple(Path.home() / word[2:] if word.startswith("~/") else word for word in words)


def ffmpeg_install() -> str | None:
    """The one-line FFmpeg install where it is unambiguous (Homebrew on macOS)."""
    return "brew install ffmpeg" if sys.platform == "darwin" else None


def needs_ffmpeg(error: dict) -> bool:
    message = str(error.get("message") or "").lower()
    return error.get("code") == "DEPENDENCY_REQUIRED" and (
        "ffmpeg" in message or "ffprobe" in message
    )


# The longest label that next steps line their commands up after.
ALIGNED_LABEL = 36


def render_error(term: Terminal, error: dict) -> None:
    if error.get("code") in CALM_ERRORS:
        return render_calm(term, error)
    code = str(error.get("code") or "REQUEST_FAILED")
    message = str(error.get("message") or "The request failed.")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    # An error may name its own title ("Can't read that tracklist") within its code. The code
    # stays in the JSON envelope; people read the title.
    heading = details.get("title") if isinstance(details.get("title"), str) else None
    title = Text.assemble((heading or ERROR_TITLES.get(code) or humanize(code.lower()), "bold bad"))
    hanging(term.err, Text(term.glyph("bad"), style="bad"), title)
    term.err.print(indented(Text(message)))
    numbered_steps(term, error)
    hint = ERROR_HINTS.get(code)
    if code == "WORKSPACE_REQUIRED":
        hint = ("Create a workspace first", first_step())
    if code == "SOURCE_NOT_ALLOWED" and isinstance(details.get("path"), str) and details["path"]:
        hint = ("Allow the folder first", ("roots", "add", Path(details["path"])))
    if isinstance(details.get("next"), str) and details["next"]:
        # The exact command for this error, unbroken under its label.
        label = details.get("next_label") or (hint[0] if hint else "Next")
        hint = (str(label), details["next"])
    if needs_ffmpeg(error) and ffmpeg_install():
        hint = ("Install FFmpeg, then retry", ffmpeg_install())
    # More than one way forward: each on its own line, commands lined up; a note (what to put
    # in place of LINK) goes under its command.
    hints = [
        (str(step.get("label") or "Next"), str(step["command"]), str(step.get("note") or ""))
        for step in details.get("next_steps") or []
        if isinstance(step, dict) and step.get("command")
    ] or ([(*hint, "")] if hint else [])
    lines = [
        (
            label,
            "" if not parts else parts if isinstance(parts, str) else term.command(*parts),
            note,
        )
        for label, parts, note in hints
    ]
    # Commands line up after the labels only when every step fits on one line and no long
    # label pushes the short ones far right; otherwise each command goes under its label.
    commands = [(label, command) for label, command, _ in lines if command]
    width = max((len(label) for label, _ in commands), default=0)
    aligned = all(width + len(command) + 8 <= term.err.width for _, command in commands) and (
        len(commands) < 2 or width <= ALIGNED_LABEL
    )
    for label, command, note in lines:
        if command and aligned:
            line = Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), label)
            line.append(" " * (width - len(label) + 2))
            line.append(command, style="cmd")
            term.err.print(line, soft_wrap=True)
        else:
            # A long label wraps under itself; the command stays in one piece for copying.
            hanging(term.err, Text(term.glyph("arrow"), style="accent"), Text(label), indent=2)
            if command:
                term.err.print(Text.assemble("    ", (command, "cmd")), soft_wrap=True)
        if note:
            term.err.print(indented(Text(note, style="muted"), 4))
    if not hints and error.get("retryable"):
        term.err.print(
            Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), "This is safe to retry.")
        )


# A normal state that ends a command, not a failure: no red, no code. The exit code and the
# JSON stay those of an error, so scripts branch the same way.
CALM_ERRORS = frozenset({"SHAZAM_NOTHING_NEW"})


def render_calm(term: Terminal, error: dict) -> None:
    """ "○ Nothing new since Sat 10 Oct 22:44", what to do, then the next command."""
    code = str(error.get("code") or "")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    title = str(details.get("headline") or ERROR_TITLES.get(code) or humanize(code.lower()))
    message = str(error.get("message") or "").removeprefix(f"{title}. ")
    hanging(term.err, Text(term.glyph("todo"), style="muted"), Text(title, style="bold"))
    if message:
        term.err.print(indented(Text(message)))
    if isinstance(details.get("next"), str) and details["next"]:
        label, command = str(details.get("next_label") or "Next"), details["next"]
        line = Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), label)
        line.append("  " if len(label) + len(command) + 8 <= term.err.width else "\n    ")
        term.err.print(line.append(command, style="cmd"), soft_wrap=True)


def numbered_steps(term: Terminal, error: dict) -> None:
    """``details.setup`` (how to turn something on) as numbered steps under the message."""
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    steps = details.get("setup")
    if not isinstance(steps, list):
        return
    for number, step in enumerate(steps, 1):
        hanging(term.err, Text(f"{number}.", style="muted"), Text(str(step)), indent=2)


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
                detail = "ctrl-c: keep going in the background"
                if job.get("state") == "queued" and not total:
                    detail = "queued · " + detail if term.unicode else "queued, " + detail
                if failed:
                    detail = f"{failed} failed"
                steps = (job.get("result") or {}).get("progress") or {}
                if job.get("kind") == "identify" and steps:
                    # An identification has no items; its listening windows show how far it got.
                    from djlib.interfaces.identify_cli import progress_line

                    done, total = steps.get("windows_done") or 0, steps.get("windows_total") or 0
                    detail = progress_line(job)
                progress.update(task, total=total or None, completed=done, detail=detail)
                if job.get("state") not in ACTIVE_STATES:
                    return Followed(job)
                if deadline is not None and time.monotonic() >= deadline:
                    return Followed(job, timed_out=True)
                time.sleep(0.25)
                job = client.request("GET", f"/jobs/{job['job_id']}")["result"]
    except KeyboardInterrupt:
        return Followed(job, detached=True)
