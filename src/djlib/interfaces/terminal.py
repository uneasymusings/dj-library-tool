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
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text
from rich.theme import Theme

OUTPUT_ENV = "DJLIB_OUTPUT"
META_KEY = "djlib.terminal"

# Named ANSI colors follow the user's terminal theme on light and dark backgrounds;
# only the brand amber is fixed, and Rich downgrades it on 256/16-color terminals.
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

    def __post_init__(self) -> None:
        self.out = Console(theme=THEME, highlight=False)
        self.err = Console(theme=THEME, highlight=False, stderr=True)

    @property
    def unicode(self) -> bool:
        return (self.out.encoding or "").lower().replace("-", "").startswith("utf")

    def glyph(self, name: str) -> str:
        fancy, plain = _GLYPHS[name]
        return fancy if self.unicode else plain

    def command(self, *parts: str) -> str:
        """A copy-pasteable command, including the workspace when it is not the default."""
        words = ["djlib"]
        if self.workspace is not None:
            words += ["--workspace", shell_path(self.workspace)]
        return " ".join(words + [quote(str(part)) for part in parts])

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

ERROR_HINTS = {
    "WORKSPACE_REQUIRED": ("Create a workspace first", ("init", "--allow-root", "PATH")),
    "WORKSPACE_NOT_EMPTY": ("Pick a new, empty folder for --workspace", None),
    "ROOTS_NOT_UPDATED": ("Add folders to an existing workspace", ("roots", "add", "PATH")),
    "SOURCE_NOT_ALLOWED": ("Allow the folder first", ("roots", "add", "PATH")),
    "SOURCE_ROOT_INVALID": ("Check that the folder exists and is a directory", None),
    "COORDINATOR_VERSION_MISMATCH": ("Stop the older background service", ("service", "stop")),
    "SERVICE_START_BUSY": ("Check the background service", ("service", "status")),
    "SERVICE_START_TIMEOUT": ("Check the background service", ("service", "status")),
    "SERVICE_START_FAILED": ("Check your setup", ("doctor",)),
    "TOKEN_MISSING": ("Check your setup", ("doctor",)),
    "DELIVERY_STALE": ("Read the current revision, then retry with it", None),
    "REVIEW_STALE": ("List open reviews for their current revision", ("reviews", "list")),
    "REQUEST_STALE": ("Read the current revision, then retry with it", None),
    "PLAN_STALE": ("Plan the collection again", None),
    "TRANSPORT_UNCERTAIN": ("Retry with the same --key; it will not run twice", None),
    "IDEMPOTENCY_CONFLICT": ("That --key was used for different input; choose a new key", None),
    "DEPENDENCY_REQUIRED": ("See what is missing", ("doctor",)),
}


def render_error(term: Terminal, error: dict) -> None:
    code = str(error.get("code") or "REQUEST_FAILED")
    message = str(error.get("message") or "The request failed.")
    title = Text.assemble(
        (f"{term.glyph('bad')} ", "bad"),
        (humanize(code.lower()), "bold bad"),
        ("  ", ""),
        (code, "muted"),
    )
    term.err.print(title)
    term.err.print(Text("  " + message), soft_wrap=True)
    hint = ERROR_HINTS.get(code)
    if hint:
        label, parts = hint
        line = Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), (label, ""))
        if parts:
            command = term.command(*parts)
            line.append("  " if len(label) + len(command) + 8 <= term.err.width else "\n    ")
            line.append(command, style="cmd")
        term.err.print(line, soft_wrap=True)
    elif error.get("retryable"):
        term.err.print(
            Text.assemble("  ", (f"{term.glyph('arrow')} ", "accent"), "This is safe to retry."),
            soft_wrap=True,
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
