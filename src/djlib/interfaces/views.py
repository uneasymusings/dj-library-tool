"""Readable terminal views for djlib results, keyed by CLI command path."""

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich import box
from rich.cells import cell_len
from rich.padding import Padding
from rich.style import Style
from rich.table import Table
from rich.text import Text

from djlib import __version__
from djlib.domain.contracts import TRAILING_VERSION, credit_form
from djlib.interfaces.client import version_key
from djlib.interfaces.terminal import (
    ACCESSIBILITY_SETTINGS,
    ACTIVE_STATES,
    JOB_LABELS,
    Terminal,
    active_context,
    ago,
    command_parts,
    duration,
    ffmpeg_install,
    follow,
    hanging,
    humanize,
    indented,
    job_progress,
    needs_ffmpeg,
    params,
    parse_time,
    path_text,
    plural,
    quote,
    render_error,
    shell_path,
    short_id,
    short_path,
    size,
)

View = Callable[[Terminal, dict], None]
VIEWS: dict[str, View] = {}

# Submissions that return a queued job; a terminal follows them with live progress.
FOLLOW = frozenset(
    {
        "scan",
        "start",
        "download",
        "export",
        "reconcile",
        "delivery prepare",
        "delivery verify-app",
        "delivery observe",
        "organize collection",
        "requests collect",
    }
)
# Machine artifacts stay JSON even in a terminal.
RAW = frozenset({"schemas", "jobs events"})


def view(*names: str):
    def register(function: View) -> View:
        for name in names:
            VIEWS[name] = function
        return function

    return register


def show(term: Terminal, name: str, envelope: dict, client_factory=None) -> None:
    """Render a response envelope; follow queued jobs for submissions."""
    if not envelope.get("ok"):
        render_error(term, envelope.get("error") or {})
        return
    if name in RAW:
        term.print_json(envelope)
        return
    result = envelope.get("result")
    if result is None:
        result = {}
    # Skipped lines and similar notes belong under the headline, not after the next steps.
    term.pending = skipped_once([str(warning) for warning in envelope.get("warnings") or []])
    if (
        name in FOLLOW
        and client_factory is not None
        and isinstance(result, dict)
        and result.get("job_id")
        and result.get("state") in ACTIVE_STATES
    ):
        followed = follow(term, client_factory(), result)
        card = crate_collected if name == "requests collect" else job_card
        card(term, followed.job, detached=followed.detached)
        flush_warnings(term)
        exit_for_job(followed.job, detached=followed.detached)
        return
    VIEWS.get(name, generic)(term, result)
    flush_warnings(term)  # views without a headline


def exit_for_job(job: dict, *, detached: bool = False, timed_out: bool = False) -> None:
    """Mirror ``jobs wait`` exit codes so terminal scripts can still branch."""
    if detached:
        raise typer.Exit(130)
    if timed_out or job.get("state") in ACTIVE_STATES:
        raise typer.Exit(3)
    if (
        job.get("state") in {"failed", "cancelled", "needs_attention", "paused"}
        or job.get("outcome") == "completed_with_gaps"
    ):
        raise typer.Exit(4)


# -- building blocks ---------------------------------------------------------------------------


# A follow-up: (label, djlib command parts), (label, "plain shell command") or (label, None).
Step = tuple[str, tuple | str | None]
# Glyph colors; only the glyph of a good result is green, its text stays plain.
TONE_STYLES = {
    "ok": "ok",
    "bad": "bad",
    "warn": "warn",
    "run": "info",
    "pause": "warn",
    "todo": "muted",
    "skip": "muted",
    "mark": "brand",
}


# "line 6 skipped (no “Artist - Title” separator): “SELF RELEASED”", or "MixesDB, line 6 …": a
# label, heading or note. Lines skipped for another reason (unreadable characters, a list cut
# short) and lines with a loose dash ("Artist -Title") may be songs, so each still prints.
SKIPPED_LINE = re.compile(
    r"^(?:[^,]+, )?line \d+ skipped \((?:no “Artist - Title” separator|looks like a heading)\)"
    r": (?P<line>.*)$"
)
LOOSE_DASH = re.compile(r"\w(?:\s[-–—]|[-–—]\s)\S")
QUOTED_SKIPS = 3


def _label_skip(warning: str) -> bool:
    match = SKIPPED_LINE.match(warning)
    return bool(match) and not LOOSE_DASH.search(match["line"])


def skipped_once(warnings: list[str]) -> list[str]:
    """More than three label lines skipped from a tracklist read as one line (JSON keeps each
    one); any other skipped line is still quoted on its own."""
    skipped = [warning for warning in warnings if _label_skip(warning)]
    if len(skipped) <= QUOTED_SKIPS:
        return warnings
    summary = f"{len(skipped)} lines without “Artist - Title” skipped (labels, headings, notes)"
    first = warnings.index(skipped[0])
    kept = [warning for warning in warnings if not _label_skip(warning)]
    return [*kept[:first], summary, *kept[first:]]


def flush_warnings(term: Terminal) -> None:
    pending, term.pending = term.pending, []
    for warning in pending:
        hanging(term.out, Text(term.glyph("warn"), style="warn"), Text(warning), indent=2)


def header(term: Terminal, title: str, subtitle: str = "", *, glyph: str = "mark") -> None:
    line = Text.assemble((title, "heading"))
    if subtitle:
        line.append(f"  {subtitle}", style="muted")
    hanging(term.out, Text(term.glyph(glyph), style=TONE_STYLES.get(glyph, "")), line)
    flush_warnings(term)


def status_line(
    term: Terminal, tone: str, message: str, detail: str = "", *, detail_style: str = "muted"
) -> None:
    """The headline: wraps under its message, never back to column 0."""
    line = Text.assemble((message, "heading"))
    if detail:
        line.append(f"  {detail}", style=detail_style)
    hanging(term.out, Text(term.glyph(tone), style=TONE_STYLES.get(tone, "")), line)
    flush_warnings(term)


def marked(term: Terminal, glyph: str, label: str, tone: str | None = None) -> Text:
    """``✓ label`` with the glyph colored; good news keeps its text plain."""
    tone = tone or glyph
    style = TONE_STYLES.get(tone, tone)
    text = Text.assemble((term.glyph(glyph), style))
    text.append(f" {label}", style="" if tone == "ok" else style)
    return text


def fields(term: Terminal, rows: list[tuple[str, object]]) -> None:
    """Aligned label/value rows; long values wrap under their value column."""
    rows = [(label, value) for label, value in rows if value is not None and value != ""]
    if not rows:
        return
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="label", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(Text(label), value if isinstance(value, Text) else Text(str(value)))
    term.out.print(indented(grid))


# Crate and request-list arguments accept the first characters of an ID (djlib.handles).
LONG_ID = re.compile(r"^(?:collection|request)_(?P<hex>[0-9a-f]{32})$")


def short_handle(part: str) -> str:
    """`collection_083659cb…` → `083659cb` in printed commands; full IDs elsewhere."""
    match = LONG_ID.match(part)
    return match["hex"][:8] if match else part


def next_steps(term: Terminal, steps: list[Step], *, title: str = "Next") -> None:
    """Copy-pasteable follow-ups; long commands move under their label, unbroken."""
    steps = list(dict.fromkeys(step for step in steps if step))
    if not steps:
        return
    term.out.print()
    term.out.print(Text(title, style="heading"))
    commands = [
        ""
        if not parts
        else parts
        if isinstance(parts, str)
        else term.command(
            *(short_handle(part) if isinstance(part, str) else part for part in parts)
        )
        for _, parts in steps
    ]
    # Align the commands of the steps that fit on one line; the rest move under their label.
    width = max(
        (
            cell_len(label)
            for (label, _), command in zip(steps, commands, strict=True)
            if 6 + cell_len(label) + cell_len(command) <= term.out.width
        ),
        default=0,
    )
    for (label, _), command in zip(steps, commands, strict=True):
        if command and 6 + max(width, cell_len(label)) + cell_len(command) <= term.out.width:
            line = Text.assemble("  ", (term.glyph("arrow"), "accent"), " ", label)
            line.append(" " * (width - cell_len(label) + 2))
            line.append(command, style="cmd")
            term.out.print(line, soft_wrap=True)
            continue
        hanging(term.out, Text(term.glyph("arrow"), style="accent"), Text(label), indent=2)
        if command:
            # Soft wrap leaves the command in one piece for copying.
            term.out.print(Text.assemble("    ", (command, "cmd")), soft_wrap=True)


def more_line(term: Terminal, summary: str, parts: tuple | None) -> None:
    """``20 of 412 tracks · more: djlib library --after X`` under a list."""
    line = Text.assemble((summary, "muted"))
    if parts:
        line.append(f"  {term.glyph('dot')}  more: ", style="muted")
        line.append(term.command(*parts), style="cmd")
    term.out.print()
    term.out.print(line, soft_wrap=True)


def note(term: Terminal, message: str) -> None:
    term.out.print(indented(Text(message, style="muted")))


MIN_FLEX = 6
READABLE_FLEX = 12  # optional columns drop before flexible text gets narrower than this
TITLE_WIDTH = 24  # ... or a title or name narrower than this; the mix name tells versions apart
GUTTER = 3  # cell padding on both sides plus SIMPLE_HEAD's blank divider


class Grid:
    """One line per row, fitted to the terminal at render time.

    Fixed columns (IDs, times, badges) keep their natural width; flexible text columns
    (``overflow`` set) shrink longest-first; ``drop`` columns disappear on narrow
    terminals, lowest number first, and when every cell is blank. A ``yields`` column (an
    explanation beside a name) gets only the room the others leave, and goes when that is
    under its readable width. A ``wrap`` column wraps its words instead of cutting them
    short, and a ``shrinks_first`` one (a short label beside a name) narrows to its readable
    width before the others do. Rich alone would hide whole columns instead. A ``gutter`` of 2
    instead of 3 gives a wide table's names one more cell per column. After rendering,
    ``shown`` names the columns that were printed.
    """

    def __init__(self, *columns: str | tuple[str, dict], gutter: int = GUTTER):
        self.columns = [column if isinstance(column, tuple) else (column, {}) for column in columns]
        self.rows: list[list] = []
        self.shown: list[str] = []
        self.gutter = gutter

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def add_row(self, *cells) -> None:
        # Plain strings would be read as Rich markup; titles like "[/]" must print as typed.
        self.rows.append([Text(cell) if isinstance(cell, str) else cell for cell in cells])

    def __rich_console__(self, console, options):
        flexible = [bool(spec.get("overflow")) for _, spec in self.columns]
        natural = []
        for index, (name, spec) in enumerate(self.columns):
            size = max(
                [cell_len(name)]
                + [
                    cell.cell_len if isinstance(cell, Text) else cell_len(str(cell))
                    for cell in (row[index] for row in self.rows)
                ]
            )
            natural.append(min(size, spec.get("max_width", size)))

        def blank(index):
            return all(
                not (cell.plain if isinstance(cell, Text) else str(cell)).strip()
                for cell in (row[index] for row in self.rows)
            )

        # An optional column with nothing in it ("You own" when nothing is owned) takes no room.
        active = [
            index
            for index, (_, spec) in enumerate(self.columns)
            if not ("drop" in spec and self.rows and blank(index))
        ]
        # Titles and names keep about 24 cells; optional columns go first.
        floor = [
            min(natural[i], spec.get("readable", READABLE_FLEX)) if flexible[i] else natural[i]
            for i, (_, spec) in enumerate(self.columns)
        ]
        for index in [i for i in active if self.columns[i][1].get("yields")]:
            others = [i for i in active if i != index]
            left = options.max_width - sum(natural[i] for i in others) - self.gutter * len(others)
            if left < floor[index]:
                active.remove(index)
            else:
                natural[index] = min(natural[index], left)

        def needed(columns):
            return sum(floor[i] for i in columns) + self.gutter * (len(columns) - 1)

        droppable = sorted(
            (spec["drop"], index)
            for index, (_, spec) in enumerate(self.columns)
            if "drop" in spec and index in active
        )
        while needed(active) > options.max_width and droppable:
            active.remove(droppable.pop(0)[1])
        widths = {i: natural[i] for i in active}
        room = options.max_width - self.gutter * (len(active) - 1)
        room -= sum(widths[i] for i in active if not flexible[i])
        shrinkable = [i for i in active if flexible[i]]
        while shrinkable and sum(widths[i] for i in shrinkable) > room:
            # Shrink whatever is furthest above its readable width; below that, the widest.
            roomy = [i for i in shrinkable if widths[i] > floor[i]]
            roomy = [i for i in roomy if self.columns[i][1].get("shrinks_first")] or roomy
            if roomy:
                widest = max(roomy, key=lambda i: widths[i] - floor[i])
            else:
                widest = max(shrinkable, key=lambda i: widths[i])
                if widths[widest] <= MIN_FLEX:
                    break
            widths[widest] -= 1
        grid = Table(
            box=box.SIMPLE_HEAD,
            header_style="label",
            show_edge=False,
            pad_edge=False,
            expand=False,
            # The divider is one blank; padding on the right only leaves two between columns.
            padding=(0, 1) if self.gutter >= GUTTER else (0, 1, 0, 0),
        )
        passthrough = {"justify", "style", "header_style"}
        for index in active:
            name, spec = self.columns[index]
            grid.add_column(
                name,
                width=widths[index],
                no_wrap=not spec.get("wrap"),
                overflow="fold" if spec.get("wrap") else "ellipsis",
                **{key: value for key, value in spec.items() if key in passthrough},
            )
        for row in self.rows:
            grid.add_row(*(row[index] for index in active))
        self.shown = [self.columns[index][0] for index in active]
        yield grid


def table(*columns: str | tuple[str, dict], gutter: int = GUTTER) -> Grid:
    return Grid(*columns, gutter=gutter)


def page_hint(term: Terminal, result: dict, noun: str, shown: int) -> None:
    total = result.get("total")
    cursor = result.get("next_cursor")
    summary = f"{shown} of {plural(total, noun)}" if isinstance(total, int) else plural(shown, noun)
    parts = None
    if cursor is not None:
        options = params()
        parts = command_parts()
        if options.get("query"):
            parts += ["--query", str(options["query"])]
        if isinstance(total, int) and total <= 100 and not options.get("after"):
            parts += ["--limit", str(total)]  # one short command instead of a long cursor
        else:
            if options.get("limit") not in (None, 20):
                parts += ["--limit", str(options["limit"])]
            parts += ["--after", str(cursor)]
        parts = tuple(parts)
    more_line(term, summary, parts)


def track_label(track: dict) -> Text:
    text = Text(track.get("artist") or "Unknown artist", style="")
    text.append(" - ")
    text.append(track.get("title") or "Untitled", style="heading")
    if track.get("version"):
        text.append(f" ({track['version']})", style="muted")
    return text


def audio_format(track: dict) -> str:
    properties = track.get("properties") or {}
    path = track.get("path") or track.get("last_known_path") or ""
    container = Path(path).suffix.lstrip(".").upper() or (properties.get("codec") or "").upper()
    codec = str(properties.get("codec") or "")
    rate = properties.get("sample_rate")
    depth = properties.get("bit_depth")
    lossless = codec.startswith("pcm") or codec in {"flac", "alac"}
    if lossless and rate:
        quality = f"{rate / 1000:g}k" + (f"/{depth}" if depth else "")
    elif properties.get("bitrate_bps"):
        quality = f"{round(properties['bitrate_bps'] / 1000)}k"
    else:
        quality = ""
    return f"{container} {quality}".strip()


def file_tail(path: str | None) -> Text:
    if not path:
        return Text("no location", style="muted")
    parts = Path(path).parts
    tail = "/".join(parts[-2:]) if len(parts) > 1 else path
    return Text(tail, style="path")


def badge(term: Terminal, state: str | None, outcome: str | None = None) -> Text:
    if state == "completed":
        if outcome == "completed_with_gaps":
            return marked(term, "warn", "partial")
        return marked(term, "ok", "done")
    glyph, label = {
        "queued": ("todo", "queued"),
        "running": ("run", "running"),
        "paused": ("pause", "paused"),
        "needs_attention": ("warn", "needs review"),
        "failed": ("bad", "failed"),
        "cancelled": ("skip", "cancelled"),
    }.get(state or "", ("todo", state or "unknown"))
    return marked(term, glyph, label)


COUNT_TONES = {
    "succeeded": "",
    "failed": "bad",
    "skipped": "warn",
    "needs_input": "warn",
    "cancelled": "muted",
    "pending": "muted",
    "running": "info",
}


def counts_text(term: Terminal, counts: dict | None) -> Text:
    counts = counts or {}
    text = Text()
    for state in ("succeeded", "failed", "skipped", "needs_input", "running", "pending"):
        amount = int(counts.get(state, 0))
        if not amount:
            continue
        if text:
            text.append(f" {term.glyph('dot')} ", style="muted")
        label = "need review" if state == "needs_input" else state
        text.append(f"{amount} {label}", style=COUNT_TONES[state])
    for state, amount in counts.items():
        if state not in COUNT_TONES and amount:
            if text:
                text.append(f" {term.glyph('dot')} ", style="muted")
            text.append(f"{amount} {state}", style="muted")
    return text


# -- jobs --------------------------------------------------------------------------------------


CRATE_JOBS = frozenset({"collection", "organize", "organization"})
NEEDS_FFMPEG = "NEEDS_FFMPEG"  # failure group, not an error code


def job_error(job: dict) -> dict:
    """The job-level error (e.g. ITEM_LIMIT for a scan), if the whole job failed."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    error = result.get("error")
    return error if isinstance(error, dict) else {"message": str(error)} if error else {}


def failed_items(job_id: str, limit: int = 100) -> list[dict]:
    """Failed items of a finished job, best effort; the card falls back to plain counts."""
    ctx = active_context()
    workspace = ctx.find_root().obj if ctx is not None else None
    if not job_id or workspace is None:
        return []
    from djlib.interfaces.client import LocalClient
    from djlib.workspace import Workspace

    if not isinstance(workspace, Workspace):
        return []
    try:
        reply = LocalClient(workspace, allow_start=False).request(
            "GET", f"/jobs/{job_id}/items", params={"state": "failed", "limit": limit}
        )
    except Exception:  # noqa: BLE001 - the card still renders without reasons
        return []
    return (reply.get("result") or {}).get("items") or []


def failure_groups(job: dict) -> list[tuple[str, str, int]]:
    """``(code, first message, count)`` for a finished job's failed items, commonest first."""
    failed = int((job.get("counts") or {}).get("failed", 0))
    if not failed or job.get("state") in ACTIVE_STATES:
        return []
    groups: dict[str, list] = {}
    items = failed_items(job.get("job_id", ""))
    for item in items:
        error = (item.get("result") or {}).get("error") or {}
        if not isinstance(error, dict):
            error = {"message": str(error)}
        code = NEEDS_FFMPEG if needs_ffmpeg(error) else str(error.get("code") or "FAILED")
        groups.setdefault(code, [str(error.get("message") or "failed"), 0])[1] += 1
    if len(groups) == 1 and len(items) < failed:
        next(iter(groups.values()))[1] = failed  # one reason for every failure we sampled
    return sorted(
        ((code, message, count) for code, (message, count) in groups.items()),
        key=lambda group: -group[2],
    )


def job_headline(kind: str, job: dict) -> str:
    counts = job.get("counts") or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    succeeded = int(counts.get("succeeded", 0))
    if kind == "scan":
        new = result.get("new_tracks")
        message = f"{plural(succeeded, 'track')} indexed"
        if isinstance(new, int) and new < succeeded:
            message = f"{plural(succeeded, 'track')}, {new} new"
    elif kind in CRATE_JOBS:
        tracks = result.get("selected_count")
        message = f"Crate built: {plural(int(succeeded if tracks is None else tracks), 'track')}"
    else:
        message = {
            "download": "Downloads finished",
            "export": "Export ready",
            "delivery": "Working copies prepared",
            "delivery_check": "Delivery check recorded",
            "reconcile": "Changes reconciled",
            "identify": "Set identified",
        }.get(kind, f"{JOB_LABELS.get(kind, humanize(kind)).split(' ', 1)[0]} finished")
    if job.get("outcome") == "completed_with_gaps":
        gaps = [f"{counts[state]} {state}" for state in ("failed", "skipped") if counts.get(state)]
        message += ", " + (" and ".join(gaps) if gaps else "with gaps")
    return message


def job_card(term: Terminal, job: dict, *, detached: bool = False, timed_out: bool = False):
    kind = job.get("kind") or "job"
    label = JOB_LABELS.get(kind, humanize(kind))
    state, outcome = job.get("state"), job.get("outcome")
    counts = job.get("counts") or {}
    if state == "completed":
        tone = "warn" if outcome == "completed_with_gaps" else "ok"
        message = job_headline(kind, job)
    elif state in ACTIVE_STATES:
        tone, message = "run", f"{label} {'(detached)' if detached else 'in progress'}"
    else:
        tone = {"failed": "bad", "cancelled": "skip", "paused": "pause"}.get(state, "warn")
        message = {
            "failed": f"{label} failed",
            "cancelled": f"{label} cancelled",
            "paused": f"{label} paused",
            "needs_attention": f"{label} needs your review",
        }.get(state, f"{label}: {state}")
    status_line(term, tone, message)
    groups = failure_groups(job)
    troubled = bool(
        counts.get("failed")
        or state in {"failed", "needs_attention"}
        or outcome == "completed_with_gaps"
    )
    done, total = job_progress(counts)
    rows: list[tuple[str, object]] = []
    # Scan and crate headlines already carry the counts once the job is done.
    if total and (state != "completed" or kind not in {"scan", *CRATE_JOBS}):
        progress = counts_text(term, counts)
        if state in ACTIVE_STATES:
            progress = Text(f"{done}/{total}  ", style="").append_text(progress)
        rows.append(("Items", progress))
    noun = "file" if kind == "scan" else "track"
    for index, (code, reason, count) in enumerate(groups):
        text = (
            Text(f"{plural(count, noun)} need FFmpeg", style="bad")
            if code == NEEDS_FFMPEG
            else Text.assemble((f"{plural(count, noun)}  ", "bad"), reason)
        )
        rows.append(("Failed" if index == 0 else "", text))
    rows += job_result_rows(term, kind, job.get("result") or {})
    if troubled:
        rows.append(("Job", Text(job.get("job_id", ""), style="muted")))
        if job.get("updated_at"):
            rows.append(("Updated", Text(ago(job["updated_at"]), style="muted")))
    fields(term, rows)
    if detached:
        note(term, "The job keeps running in the background; closing this terminal is safe.")
    elif timed_out:
        note(term, "Still running in the background.")
    next_steps(term, job_next_steps(term, job, groups))


def job_result_rows(term: Terminal, kind: str, result: dict) -> list[tuple[str, object]]:
    if not isinstance(result, dict):
        return []
    rows: list[tuple[str, object]] = []
    if kind == "identify" and isinstance(result.get("tracklist"), dict):
        from djlib.application.identification import summary

        found = summary(result)
        rows += [
            ("Set", Text((result.get("source") or {}).get("title") or "")),
            ("Tracklist", Text(heard_tally(term, found["counts"], found["recognizer"]))),
            ("Saved to", path_text(result.get("tracklist_file"))),
        ]
    if kind == "export":
        rows += [
            ("Tracks", result.get("track_count")),
            ("Playlist", path_text(result.get("playlist_path"))),
            ("rekordbox XML", path_text(result.get("rekordbox_xml_path"))),
            ("Manifest", path_text(result.get("manifest_path"))),
        ]
        return rows
    if kind in {"organize", "organization"} and result.get("excluded_count"):
        reasons = ", ".join(
            f"{count} {humanize(reason).lower()}"
            for reason, count in (result.get("exclusion_counts") or {}).items()
        )
        rows.append(("Excluded", Text(f"{result['excluded_count']}  {reasons}", "warn")))
    skipped = result.get("skipped_files") or {}
    if kind == "scan" and sum(skipped.values()):
        reasons = []
        if skipped.get("workspace_files"):
            reasons.append(f"{skipped['workspace_files']} djlib working copies")
        if skipped.get("outside_allowed_folders"):
            reasons.append(f"{skipped['outside_allowed_folders']} links outside allowed folders")
        rows.append(("Skipped", Text(", ".join(reasons), style="muted")))
    if kind in CRATE_JOBS and result.get("name"):
        rows.append(("Crate", Text(result["name"])))
    elif result.get("collection_id"):
        rows.append(("Crate", Text(result["collection_id"], style="")))
    if result.get("delivery_id"):
        rows.append(("Delivery", Text(result["delivery_id"], style="")))
    for index, playlist in enumerate(result.get("playlists") or []):
        rows.append(("Playlists" if index == 0 else "", path_text(playlist.get("path"))))
    if result.get("error"):
        error = result["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        rows.append(("Error", Text(str(message), style="bad")))
    return rows


def job_next_steps(
    term: Terminal, job: dict, groups: list[tuple[str, str, int]] | None = None
) -> list[Step]:
    job_id = job.get("job_id", "")
    state, kind = job.get("state"), job.get("kind")
    counts = job.get("counts") or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    steps: list[Step] = []
    if state in ACTIVE_STATES:
        steps.append(("Watch progress", ("jobs", "watch", job_id)))
        return steps
    if state == "needs_attention" or counts.get("needs_input"):
        steps.append(("Review conflicts", ("reviews", "list", "--job-id", job_id)))
    if state == "paused":
        steps.append(("Resume", ("jobs", "control", job_id, "resume")))
    error = job_error(job)
    retry = ("jobs", "control", job_id, "retry")
    codes = {code for code, _, _ in groups or []}
    if error.get("code") == "ITEM_LIMIT" and kind == "scan":
        # Retrying the same folder hits the same limit.
        steps.append(("Scan one subfolder at a time", ("scan", "PATH")))
    elif needs_ffmpeg(error) or NEEDS_FFMPEG in codes:
        steps.append(("Install FFmpeg", ffmpeg_install() or ("doctor",)))
        if codes - {NEEDS_FFMPEG}:
            steps.append(("See the other failures", ("jobs", "items", job_id, "--state", "failed")))
        steps.append(("Then retry the failed files", retry))
    elif counts.get("failed"):
        steps.append(("See what failed", ("jobs", "items", job_id, "--state", "failed")))
        if state != "cancelled":
            steps.append(("Retry failed items", retry))
    elif state == "failed":
        steps.append(("Try again", retry))
    if state == "completed":
        if kind == "identify" and (result.get("source") or {}).get("url"):
            url = result["source"]["url"]
            steps.append(("Check it against your music", ("set", url, "--no-rekordbox")))
        elif kind == "scan":
            steps.append(("Browse your library", ("library",)))
            steps.append(("Copy a set link or tracklist, then", ("set", "paste")))
        elif result.get("collection_id"):
            collection = result["collection_id"]
            if kind in CRATE_JOBS:
                steps.append(("Put it in rekordbox", ("rekordbox", "push", collection)))
                steps.append(("Or straight onto your USB", ("rekordbox", "usb", collection)))
                steps.append(("See its tracks", ("crate", collection)))
            else:
                steps.append(("Open the crate", ("crate", collection)))
        elif kind in {"delivery", "delivery_check"} and result.get("delivery_id"):
            steps.append(("Check delivery status", ("delivery", "get", result["delivery_id"])))
        elif kind == "export" and result.get("playlist_path"):
            steps.append(("Import the playlist in rekordbox or Serato", None))
    return steps


@view("jobs get", "jobs control", "reviews resolve")
def jobs_get(term: Terminal, job: dict) -> None:
    job_card(term, job)


@view("jobs list")
def jobs_list(term: Terminal, result: dict) -> None:
    rows = result.get("jobs") or []
    if not rows:
        status_line(term, "todo", "No jobs yet")
        next_steps(term, [("Index your music", ("scan",))])
        return
    grid = table(
        ("State", {}),
        ("Kind", {}),
        ("Name", {"overflow": "ellipsis", "readable": TITLE_WIDTH}),
        ("Items", {"overflow": "ellipsis", "drop": 2}),
        ("Updated", {"style": "muted", "drop": 1}),
        ("Job", {"style": "muted"}),
    )
    for job in rows:
        grid.add_row(
            badge(term, job.get("state"), job.get("outcome")),
            humanize(job.get("kind") or ""),
            job.get("name") or "",
            counts_text(term, job.get("counts")),
            ago(job.get("updated_at")),
            short_id(job.get("job_id")),
        )
    term.out.print(grid)
    page_hint(term, result, "job", len(rows))


@view("jobs items")
def jobs_items(term: Terminal, result: dict) -> None:
    rows = result.get("items") or []
    if not rows:
        status_line(term, "todo", "No matching items")
        return
    grid = table(
        ("#", {"justify": "right", "style": "muted"}),
        ("State", {}),
        ("Item", {"overflow": "fold"}),
        ("Detail", {"overflow": "fold"}),
    )
    for item in rows:
        source = item.get("input") or {}
        label = (
            track_label(source)
            if isinstance(source, dict) and (source.get("artist") or source.get("title"))
            else Text(str(source.get("url") or source.get("path") or "") if source else "")
        )
        outcome = item.get("result") or {}
        detail = ""
        if isinstance(outcome, dict):
            error = outcome.get("error")
            if isinstance(error, dict):
                detail = error.get("message", "")
            elif error:
                detail = str(error)
            elif outcome.get("reason"):
                detail = str(outcome["reason"])
        grid.add_row(
            str(item.get("position", "")),
            item_badge(term, item.get("state")),
            label,
            Text(detail, style="bad" if item.get("state") == "failed" else "muted"),
        )
    term.out.print(grid)
    if result.get("next_cursor") is not None:
        options = params()
        parts = ("jobs", "items", options.get("job_id", ""), "--after", str(result["next_cursor"]))
        if options.get("state"):
            parts += ("--state", str(options["state"]))
        more_line(term, plural(len(rows), "item"), parts)


def item_badge(term: Terminal, state: str | None) -> Text:
    glyph, tone = {
        "succeeded": ("ok", "ok"),
        "failed": ("bad", "bad"),
        "skipped": ("skip", "warn"),
        "needs_input": ("warn", "warn"),
        "running": ("run", "run"),
        "pending": ("todo", "todo"),
        "cancelled": ("skip", "skip"),
    }.get(state or "", ("todo", "todo"))
    label = "needs review" if state == "needs_input" else (state or "")
    return marked(term, glyph, label, tone)


# -- reviews -----------------------------------------------------------------------------------


@view("reviews list")
def reviews_list(term: Terminal, result: dict) -> None:
    rows = result.get("reviews") or []
    if not rows:
        status_line(term, "ok", "Nothing to review")
        return
    header(term, "Metadata conflicts", plural(len(rows), "open review"))
    for review in rows:
        term.out.print()
        evidence = review.get("evidence") or {}
        requested = evidence.get("requested") or evidence.get("track") or {}
        found = evidence.get("file_metadata") or {}
        grid = table(("", {"style": "label"}), "You asked for", "File says")
        for key in ("artist", "title", "version"):
            ask, got = str(requested.get(key) or ""), str(found.get(key) or "")
            if ask or got:
                grid.add_row(humanize(key), ask, Text(got, style="warn" if ask != got else ""))
        term.out.print(Padding(Text(humanize(str(review.get("reason") or "conflict"))), (0, 2)))
        if grid.row_count:
            term.out.print(Padding(grid, (0, 0, 0, 2)))
        next_steps(
            term,
            [
                (
                    humanize(choice),
                    (
                        "reviews",
                        "resolve",
                        review.get("review_id", ""),
                        "--revision",
                        str(review.get("revision", "")),
                        "--choice",
                        choice,
                    ),
                )
                for choice in review.get("choices") or []
            ],
            title="Choose one",  # one per review; "Next" stays one block per screen
        )


# -- catalog -----------------------------------------------------------------------------------


def track_table(
    term: Terminal, tracks: list[dict], *, numbered: bool = False, start: int = 0
) -> Table:
    """One line per track; columns that are empty for every row are left out."""
    show_version = any(track.get("version") for track in tracks)
    show_dj = any(
        (track.get("dj") or {}).get("bpm") or (track.get("dj") or {}).get("key") for track in tracks
    )
    # File names only fit beside full titles on wide terminals.
    show_file = not numbered and term.out.width >= 120
    columns: list[tuple[str, dict]] = []
    if numbered:
        columns.append(("#", {"justify": "right", "style": "muted"}))
    columns += [
        ("Artist", {"overflow": "ellipsis"}),
        ("Title", {"overflow": "ellipsis", "style": "heading", "readable": TITLE_WIDTH}),
    ]
    if show_version:
        columns.append(("Version", {"overflow": "ellipsis"}))
    if show_dj:
        columns += [("BPM", {"justify": "right"}), ("Key", {})]
    columns += [
        ("Time", {"justify": "right"}),
        ("Format", {"style": "muted", "drop": 2}),
    ]
    if show_file:
        columns.append(("File", {"overflow": "ellipsis", "drop": 1}))
    grid = table(*columns)
    for index, track in enumerate(tracks, start + 1):
        properties = track.get("properties") or {}
        cells: list[object] = [str(index)] if numbered else []
        cells += [track.get("artist") or "", track.get("title") or ""]
        if show_version:
            cells.append(Text(track.get("version") or "", style="muted"))
        if show_dj:
            dj = track.get("dj") or {}
            bpm = dj.get("bpm")
            cells += [f"{bpm:g}" if isinstance(bpm, int | float) else "", dj.get("key") or ""]
        cells += [duration(properties.get("duration_seconds")), audio_format(track)]
        if show_file:
            cells.append(file_tail(track.get("path") or track.get("last_known_path")))
        grid.add_row(*cells)
    return grid


@view("library")
def library(term: Terminal, result: dict) -> None:
    tracks = result.get("tracks") or []
    if not tracks:
        query = params().get("query")
        if query:
            status_line(term, "todo", "No tracks match", f'"{query}"')
        else:
            status_line(term, "todo", "Your library is empty")
            next_steps(term, [("Index your music", ("scan",))])
        return
    term.out.print(track_table(term, tracks))
    page_hint(term, result, "track", len(tracks))


# `crates`/`crate` are the command names; `collections`/`collection` remain as aliases.
@view("crates", "collections")
def collections(term: Terminal, result: dict) -> None:
    rows = result.get("collections") or []
    if not rows:
        status_line(term, "todo", "No crates yet")
        note(term, "A crate is built from the songs of a tracklist you own.")
        next_steps(term, [("Copy a set link or tracklist, then", ("set", "paste"))])
        return
    grid = table(
        ("Name", {"style": "heading", "overflow": "ellipsis", "readable": TITLE_WIDTH}),
        ("Tracks", {"justify": "right"}),
        # Where it went matters more than when it was made: Created goes first.
        ("rekordbox / USB", {"overflow": "ellipsis", "drop": 2, "readable": 28}),
        ("Created", {"style": "muted", "drop": 1}),
        ("ID", {"style": "muted"}),
    )
    for row in rows:
        # What djlib did with it. rekordbox isn't read here, so a push is "pushed … ago" (the
        # DJ may have deleted the playlist since), never "✓ in rekordbox" as status can say.
        done = row.get("rekordbox") if isinstance(row.get("rekordbox"), dict) else {}
        places = place_parts(term, {"usb": done.get("usb")}, app=False)
        if done.get("pushed_at"):
            places.insert(0, Text(f"pushed {ago(done['pushed_at'])}", style="muted"))
        grid.add_row(
            row.get("name") or "",
            str(row.get("track_count", "")),
            Text("  ").join(places),
            ago(row.get("created_at")),
            short_id(row.get("collection_id")),
        )
    term.out.print(grid)
    page_hint(term, result, "crate", len(rows))


@view("crate", "collection")
def collection(term: Terminal, result: dict) -> None:
    tracks = result.get("tracks") or []
    total = result.get("track_count", len(tracks))
    header(term, result.get("name") or "Crate", plural(total, "track"))
    offset = int(params().get("after") or 0)
    if tracks:
        term.out.print()
        term.out.print(track_table(term, tracks, numbered=True, start=offset))
    if result.get("next_cursor") is not None:
        more_line(
            term,
            f"{offset + 1}{'–' if term.unicode else '-'}{offset + len(tracks)} "
            f"of {plural(total, 'track')}",
            (
                "crate",
                short_handle(result.get("collection_id", "")),
                "--after",
                str(result["next_cursor"]),
            ),
        )
    collection = result.get("collection_id", "")
    next_steps(
        term,
        [
            ("Put it in rekordbox", ("rekordbox", "push", collection)),
            ("Or straight onto your USB", ("rekordbox", "usb", collection)),
        ]
        if tracks
        else [],
    )


@view("roots list", "roots add")
def roots(term: Terminal, result: dict) -> None:
    allowed = result.get("allowed_roots") or []
    added = set(result.get("added") or [])
    if added:
        status_line(term, "ok", f"Allowed {plural(len(added), 'folder')}")
        term.out.print()
    if not allowed:
        status_line(term, "todo", "No music folders allowed yet")
        next_steps(term, [("Allow your music folder", ("roots", "add", Path.home() / "Music"))])
        return
    header(term, "Music folders", "djlib only reads inside these")
    for root in allowed:
        exists = Path(root).is_dir()
        line = Text.assemble((short_path(root), "path"))
        if root in added:
            line.append("  new", style="accent")
        if not exists:
            line.append("  not found (unplugged drive?)", style="bad")
        glyph = "ok" if exists else "bad"
        hanging(term.out, Text(term.glyph(glyph), style=glyph), line, indent=2)
    if added and len(added) == 1:
        next_steps(term, [("Index it", ("scan", Path(next(iter(added)))))])


# -- setup and service -------------------------------------------------------------------------


@view("version")
def version_view(term: Terminal, result: dict) -> None:
    header(term, "djlib", result.get("version", __version__))


@view("init")
def init_view(term: Terminal, result: dict) -> None:
    config = result.get("config") or {}
    allowed = config.get("allowed_roots") or []
    added = result.get("added") or []
    if result.get("created") is False and added:
        # init on an existing setup: the new folders are what changed.
        headline = f"Added {short_path(added[0])} to your music folders"
        if len(added) == 1 and cell_len(headline) + 2 <= term.out.width:
            status_line(term, "ok", headline)
        else:
            # Several folders, or one long path: each on its own line.
            many = f"Added {len(added)} music folders"
            status_line(term, "ok", many if len(added) > 1 else "Added to your music folders")
            for root in added:
                term.out.print(indented(path_text(root)))
        next_steps(term, [("Index the new music", ("scan",))])
        return
    # Where djlib keeps its own files is for `djlib doctor`; a DJ sees the music.
    header(term, "Ready")
    fields(
        term,
        [("Music" if i == 0 else "", path_text(root)) for i, root in enumerate(allowed)],
    )
    steps = (
        [("Index your music", ("scan",))]
        if allowed
        else [("Add your music folder", ("init", Path.home() / "Music"))]
    )
    steps.append(("Check your setup", ("doctor",)))
    next_steps(term, steps)


@view("ui")
def review_page(term: Terminal, result: dict) -> None:
    # Only claim the browser opened when the command says it did.
    header(term, "Review page", "opened in your browser" if result.get("opened") else "")
    term.out.print(Text("  " + (result.get("url") or ""), style="path"), soft_wrap=True)
    term.out.print()
    note(term, "The link contains this workspace's access token. Keep it to yourself.")
    note(term, "It works while djlib's background service is running.")


@view("status")
def status_view(term: Terminal, result: dict) -> None:
    tracks = int(result.get("tracks") or 0)
    header(term, "djlib", short_path(result.get("workspace")))
    bpm, key = int(result.get("with_bpm") or 0), int(result.get("with_key") or 0)
    dot = term.glyph("dot")
    fields(
        term,
        [
            (
                "Library",
                Text.assemble(
                    (plural(tracks, "track"), "heading"),
                    f"  {dot}  BPM for {bpm}  {dot}  key for {key}"
                    if bpm or key
                    else (f"  {dot}  no BPM yet" if tracks else ""),
                ),
            ),
            (
                "Analysis",
                Text(f"rekordbox synced {ago(result['rekordbox_analysis_synced_at'])}", "muted")
                if result.get("rekordbox_analysis_synced_at")
                else None,
            ),
        ],
    )
    # A tracklist checked again after edits makes a new set, and a rebuilt crate a new
    # collection; show the newest set of each name, with the newest crate made from it.
    newest: dict[str, dict] = {}
    for row in result.get("recent_requests") or []:
        newest.setdefault(row.get("name") or row.get("request_id"), row)
    requests = list(newest.values())
    recent = result.get("recent_collections") or []
    made = set_crates(requests, recent)
    # A set that owns more songs than its crate holds was re-checked after new music. Only
    # songs with one verified copy ("owned" here) go into a crate.
    outdated = {
        key: row
        for row in requests
        if (key := row.get("request_id")) in made
        and crate_size(row) > int(made[key].get("tracks") or 0)
    }
    # Other crates: the newest of each name that no set's line already shows.
    paired = {row.get("collection_id") for row in made.values()}
    names = {row.get("name") for row in made.values()}
    crates: dict[str, dict] = {}
    for row in recent:
        if row.get("collection_id") not in paired and row.get("name") not in names:
            crates.setdefault(row.get("name") or row.get("collection_id"), row)
    others = list(crates.values())
    listed = paired | {row.get("collection_id") for row in others}
    collections = [row for row in recent if row.get("collection_id") in listed]
    unread = result.get("rekordbox_checked") is False
    app = rekordbox_app(result)
    for title, rows in (("Sets", requests), ("Other crates" if requests else "Crates", others)):
        if not rows:
            continue
        term.out.print()
        term.out.print(Text(title, style="heading"))
        for row in rows:
            if title == "Sets":
                key = row.get("request_id")
                set_line(term, row, made.get(key), outdated.get(key), app=app)
            else:
                term.out.print(indented(crate_line(term, row, None, app=app)))
    if unread and app and collections:
        # Its playlists can only be read while it runs; "not in rekordbox yet" is djlib's.
        if result.get("rekordbox_open") is False:
            note(term, "(rekordbox is closed, so djlib can't tell which are in it)")
        else:
            note(
                term,
                "(djlib can't see rekordbox's playlists right now, so it can't tell "
                "which are in it)",
            )
    steps = status_steps(result, requests, collections, outdated, made)
    if not steps:
        steps.append(("Copy a set link or tracklist, then", ("set", "paste")))
    next_steps(term, steps)


def rekordbox_app(result: dict) -> bool:
    """Whether djlib can put a crate in rekordbox here (the app on macOS); a status from before
    djlib said so counts it when it could read rekordbox's playlists."""
    return bool(result.get("rekordbox_app", result.get("rekordbox_checked")))


def set_counts(row: dict) -> dict:
    """Owned, missing, other mix and ID counts of a saved set; older services send only the
    first two (and songs)."""
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    found = {
        key: int(counts.get(key) or 0)
        for key in ("owned", "missing", "other_mix", "ids", "to_check", "maybe")
    }
    if not counts:
        found.update(owned=int(row.get("owned") or 0), missing=int(row.get("missing") or 0))
    songs = row.get("songs", row.get("total_items"))
    found["songs"] = int(songs) if isinstance(songs, int) else sum(found.values())
    return found


def in_crate(items: list[dict]) -> int:
    """How many files a crate from these items holds (catalog_paging.in_crate)."""
    return len(
        {
            (item.get("accepted") or {}).get("asset_revision_id") or item.get("item_id")
            for item in items
            if item.get("state") == "satisfied"
        }
    )


def crate_size(row: dict) -> int:
    """How many tracks a crate from this set holds (one file per song, however often the set
    lists it); services before 0.1.0a16 count owned songs."""
    return int(row.get("in_crate", row.get("owned")) or 0)


def set_crates(requests: list[dict], collections: list[dict]) -> dict[str, dict]:
    """The newest crate collected from each set, by set (request) ID.

    Every Shazam import is called "Shazam", so a crate goes with the set it was collected
    from. Services before 0.1.0a16 don't say which that was; pair those by name.
    """
    by_name = {row.get("name"): row.get("request_id") for row in requests}
    shown = set(by_name.values())
    made: dict[str, dict] = {}
    for row in collections:  # newest first
        source = row["request_id"] if "request_id" in row else by_name.get(row.get("name"))
        if source in shown:
            made.setdefault(source, row)
    return made


def status_tally(term: Terminal, counts: dict) -> list[Text]:
    """``12 of 22 owned``, ``1 to check``, ``8 missing``, ``2 in another mix``, ``2 IDs``: the
    parts that are there, counted as `djlib set` counts them (an owned song waiting on a pick
    or an unreadable file is "to check", not owned yet)."""
    owned = counts["owned"] - counts.get("to_check", 0)
    parts = [Text(f"{owned} of {counts['songs']} owned")]
    for key, label, style in (
        ("to_check", "to check", "warn"),
        ("maybe", "maybe yours", "warn"),
        ("missing", "missing", "warn"),
        ("other_mix", "in another mix", "warn"),
        ("ids", "IDs", "muted"),
    ):
        if amount := counts.get(key):
            parts.append(Text(f"{amount} {'ID' if key == 'ids' and amount == 1 else label}", style))
    return parts


def set_line(
    term: Terminal, row: dict, crate: dict | None, outdated: dict | None, *, app: bool = True
) -> None:
    """``○ Friday  12 of 22 owned · 10 missing · crate 12 · ✓ in rekordbox`` for status."""
    counts = set_counts(row)
    done = (
        not row.get("unresolved")
        if "unresolved" in row
        else counts["owned"] - counts["to_check"] == counts["songs"]
    )
    parts = status_tally(term, counts)
    if crate:
        parts.append(Text(f"crate {int(crate.get('tracks') or 0)}"))
        if outdated:
            parts.append(Text(f"{term.glyph('warn')} outdated", style="warn"))
        parts += place_parts(term, crate, app=app)
    glyph = Text(term.glyph("ok" if done else "todo"), style="ok" if done else "muted")
    name = Text(str(row.get("name") or ""), style="heading")
    # Indent, glyph and the gap after it.
    width = term.out.width - 2 - glyph.cell_len - 1
    line = unbroken(name, parts, Text(f" {term.glyph('dot')} ", style="muted"), width)
    hanging(term.out, glyph, line, indent=2)


def unbroken(first: Text, parts: list[Text], sep: Text, width: int) -> Text:
    """``first  part · part · part`` wrapped between parts only, so "crate 12" or "7 missing"
    never splits over two lines."""
    line, used = first.copy(), first.cell_len
    for index, part in enumerate(parts):
        joint = Text("  ") if index == 0 else sep
        if used and used + joint.cell_len + part.cell_len > width:
            line.append("\n")
            used = 0
        else:
            line.append_text(joint)
            used += joint.cell_len
        line.append_text(part)
        used += part.cell_len
    return line


def crate_line(term: Terminal, row: dict, outdated: dict | None, *, app: bool = True) -> Text:
    """``Friday  8 tracks  ✓ in rekordbox  ✓ on RICARDO_AM 1d ago`` for the status screen.
    Without rekordbox to put it in (``app`` False), a crate says nothing about rekordbox."""
    # The rekordbox playlist name tells versions of one set apart ("Friday (2)").
    line = Text(str(row.get("playlist") or row.get("name") or ""))
    line.append(f"  {plural(int(row.get('tracks') or 0), 'track')}", style="heading")
    if outdated:
        line.append(
            f"  {term.glyph('warn')} outdated: you now own {crate_size(outdated)} of its songs",
            style="warn",
        )
    return line.append_text(crate_places(term, row, app=app))


def crate_places(term: Terminal, row: dict, sep: Text | None = None, *, app: bool = True) -> Text:
    """``  ✓ in rekordbox  ✓ on RICARDO_AM 1d ago``: where djlib put a crate."""
    line, sep = Text(), sep or Text("  ")
    for part in place_parts(term, row, app=app):
        line.append_text(sep).append_text(part)
    return line


def place_parts(term: Terminal, row: dict, *, app: bool = True) -> list[Text]:
    """``✓ in rekordbox``, ``✓ on RICARDO_AM 1d ago``: each place djlib put a crate.

    ``in_rekordbox`` is what rekordbox shows right now (None: unread); without it, only
    that djlib pushed it once, which the DJ may have deleted since. Without rekordbox to put
    it in (``app`` False), a crate never pushed says nothing about rekordbox.
    """
    parts: list[Text] = []
    if row.get("in_rekordbox") is True:
        parts.append(marked(term, "ok", "in rekordbox"))
    elif row.get("in_rekordbox") is None and row.get("pushed_at"):
        parts.append(Text(f"pushed to rekordbox {ago(row['pushed_at'])}", style="muted"))
    elif row.get("in_rekordbox") is False or app:
        # rekordbox says it isn't there, or is closed and djlib never pushed it.
        parts.append(Text("not in rekordbox yet", style="muted"))
    usb = row.get("usb") or {}
    if usb and usb_failed(usb):
        problem = f"USB check failed on {usb.get('device')}: {usb_problem(usb)}"
        parts.append(marked(term, "bad", problem))
    elif usb:
        place = marked(term, "ok", f"on {usb.get('device')}")
        parts.append(place.append(f" {ago(usb.get('checked_at'))}", style="muted"))
    return parts


def status_steps(
    result: dict,
    requests: list[dict],
    collections: list[dict],
    outdated: dict[str, dict],
    made: dict[str, dict],
) -> list[Step]:
    """What to do next on the status screen; ``outdated`` and ``made`` (each set's crate) are
    keyed by set ID."""
    tracks = int(result.get("tracks") or 0)
    bpm, key = int(result.get("with_bpm") or 0), int(result.get("with_key") or 0)
    steps: list[Step] = []
    if not tracks:
        steps.append(("Index your music", ("scan",)))
    elif not requests and not collections:
        steps.append(("Copy a set link or tracklist, then", ("set", "paste")))
        if result.get("shazam_history_found"):
            since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            steps.append(("Your recent Shazams", ("set", f"shazam:{since}", "--no-rekordbox")))
    synced = result.get("rekordbox_analysis_synced_at")
    if tracks and not bpm and not synced and result.get("rekordbox_installed"):
        steps.append(("Read BPM and cues from rekordbox's analysis", ("rekordbox", "sync")))
    for row in outdated.values():
        # Pushing the old crate would leave the new songs out of the set.
        steps.append(
            (
                f"Rebuild “{row['name']}” with the {plural(crate_size(row), 'song')} you own",
                ("requests", "collect", row["request_id"]),
            )
        )
        break
    stale = {made[key].get("collection_id") for key in outdated}
    paired = {row.get("collection_id") for row in made.values()}
    names = {row.get("name") for row in requests}
    # A crate named like a newer set came from an older one (a Shazam import, an edited
    # tracklist): pushing it isn't putting that set anywhere.
    current = [
        row
        for row in collections
        if row.get("collection_id") not in stale
        and (row.get("collection_id") in paired or row.get("name") not in names)
    ]
    for row in current:
        # Closed rekordbox can't say; a crate djlib never pushed counts as not there yet,
        # where there is a rekordbox app to put it in.
        if row.get("in_rekordbox") is False or (
            rekordbox_app(result) and row.get("in_rekordbox") is None and not row.get("pushed_at")
        ):
            steps.append(
                (f"Put “{row['name']}” in rekordbox", ("rekordbox", "push", row["collection_id"]))
            )
            break
    for row in requests:
        if row.get("to_pick"):
            # Two copies of an owned song: checking again finds both again.
            steps.append((f"Pick which copy to use in “{row['name']}”", ("ui",)))
            break
        if row.get("needs_check"):
            # A file on a drive that isn't plugged in: check again once it is.
            steps.append((f"Re-check “{row['name']}”", ("requests", "refresh", row["request_id"])))
            break
    for row in requests:
        if row.get("owned") and not row.get("needs_check") and row.get("request_id") not in made:
            steps.append(
                (f"Make a crate from “{row['name']}”", ("requests", "collect", row["request_id"]))
            )
            break
    for row in current:
        in_rekordbox = row.get("in_rekordbox") is True or (
            row.get("in_rekordbox") is None and row.get("pushed_at")
        )
        usb = row.get("usb") or {}
        if in_rekordbox and (not usb or usb_failed(usb)):
            name = row.get("playlist") or row["name"]
            label = f"Export “{name}” to your USB again" if usb else f"Put “{name}” on your USB"
            steps.append((label, ("rekordbox", "usb", row["collection_id"])))
            break
    if tracks and bpm > key and result.get("rekordbox_checked"):
        steps.append(("Bring in musical key", ("rekordbox", "pull", "--when-idle", "120")))
    return steps


@view("use")
def use_view(term: Terminal, result: dict) -> None:
    label = {
        "DJLIB_WORKSPACE": "from DJLIB_WORKSPACE",
        "remembered": "remembered",
        "built_in": "built-in default",
    }.get(result.get("source"), "")
    status_line(
        term,
        "ok" if result.get("initialized") else "warn",
        f"Default workspace: {short_path(result.get('workspace'))}",
        label,
    )
    if not result.get("initialized"):
        next_steps(term, [("Create it", ("init", Path.home() / "Music"))])
    elif result.get("changed"):
        note(term, "Commands now use it without --workspace.")


@view("doctor")
def doctor(term: Terminal, result: dict) -> None:
    header(term, "djlib doctor", __version__)
    runtimes = result.get("javascript_runtimes") or {}
    selected = runtimes.get(runtimes.get("selected") or "") or {}
    ready = result.get("workspace_initialized") is not False
    folders = [short_path(root) for root in result.get("music_folders") or []]
    music = folders[0] if folders else ""
    if len(folders) > 1:
        music += f" and {len(folders) - 1} more"
    checks = [
        ("Music folders", ready, (music or "none yet") if ready else "not set up yet", ""),
        *(
            [("djlib's folder", True, short_path(result.get("workspace")) or "", "")]
            if ready
            else []
        ),
        (
            "Background service",
            bool(result.get("coordinator_url")),
            "running" if result.get("coordinator_url") else "not running",
            "" if result.get("coordinator_url") else "starts automatically when needed",
        ),
        (
            "FFmpeg",
            bool(result.get("ffmpeg")),
            short_path(result.get("ffmpeg")) or "missing",
            "" if result.get("ffmpeg") else "needed for MP3/FLAC/M4A/AIFF and downloads",
        ),
        (
            "ffprobe",
            bool(result.get("ffprobe")),
            short_path(result.get("ffprobe")) or "missing",
            "" if result.get("ffprobe") else "ships with FFmpeg",
        ),
        (
            "yt-dlp",
            bool(result.get("yt_dlp_installed")),
            "installed" if result.get("yt_dlp_installed") else "not installed",
            "" if result.get("yt_dlp_installed") else "optional; install the [download] extra",
        ),
        (
            # yt-dlp needs Deno or Node to read YouTube pages; the major version is enough.
            "YouTube helper",
            bool(runtimes.get("youtube_runtime_ready")),
            f"{runtimes.get('selected')} {(selected.get('version') or '').split('.')[0]}".strip()
            if runtimes.get("selected")
            else "none",
            "" if runtimes.get("youtube_runtime_ready") else "optional; Deno 2.3+ or Node 22+",
        ),
    ]
    if result.get("rekordbox_automation_allowed") is not None:
        app = result.get("rekordbox") or {}
        allowed = bool(result.get("rekordbox_automation_allowed"))
        checks[2:2] = [
            (
                "rekordbox",
                bool(app),
                f"rekordbox {app.get('version') or ''}".strip() if app else "not found",
                "" if app else "install it in /Applications to push crates and export USBs",
            ),
            (
                "App control",
                allowed,
                "allowed" if allowed else "not allowed",
                ""
                if allowed
                else "System Settings > Privacy & Security > Accessibility > your terminal",
            ),
            (
                "Screen",
                not result.get("screen_locked"),
                "locked" if result.get("screen_locked") else "unlocked",
                "unlock it so djlib can drive rekordbox" if result.get("screen_locked") else "",
            ),
            (
                "rekordbox analysis",
                bool(result.get("rekordbox_analysis_folder")),
                "found" if result.get("rekordbox_analysis_folder") else "not found",
                analysis_hint(result),
            ),
        ]
    if recognition := result.get("song_recognition"):
        checks.append(("Song recognition", *recognition_check(recognition)))
    if shazam := result.get("shazam_history"):
        checks.append(("Shazam history", *shazam_check(shazam)))
    fixes = result.get("fixes") or {}
    if plugin := result.get("claude_plugin"):
        hint = ""
        if fixes.get("claude_plugin"):
            hint = f"newer than djlib {__version__}"
            if version_key(plugin) < version_key(__version__):
                hint = f"older than djlib {__version__}; update it, then restart Claude Code"
        checks.append(("Claude Code plugin", not hint, plugin, hint))
    if not ready and not fixes.get("workspace"):
        fixes = {**fixes, "workspace": term.command("init", Path.home() / "Music")}
    width = max(cell_len(check[0]) for check in checks) + 2  # glyph and space
    for name, passed, value, hint in checks:
        # Not set up yet and not running yet are steps to take, not failures.
        todo = "optional" in hint or name in {"Background service", "Music folders"}
        tone = "ok" if passed else ("todo" if todo else "bad")
        detail = Text.assemble((value, "path" if passed and "/" in value else ""))
        if hint:
            detail.append(f"  {hint}", style="muted")
        row = Table.grid(padding=(0, 2))
        row.add_column(no_wrap=True, min_width=width)
        row.add_column(overflow="fold")
        row.add_row(marked(term, tone, name), detail)
        term.out.print(indented(row))
        fix = None if passed else fixes.get(DOCTOR_FIXES.get(name, ""))
        if name == "App control" and not passed:
            fix = fix or ACCESSIBILITY_SETTINGS
        if fix:
            # Under the detail column, unbroken so it can be pasted.
            line = Text.assemble(" " * (width + 4), (term.glyph("arrow"), "accent"), " ")
            term.out.print(line.append(str(fix), style="cmd"), soft_wrap=True)
    term.out.print()
    if result.get("required_checks_passed") is False:
        note(term, f"Fix the items marked {term.glyph('bad')}, then run djlib doctor again.")
    note(term, "djlib drives rekordbox through its own menus and never edits its database.")


# Doctor check -> key of its install/fix command in the result's ``fixes``.
DOCTOR_FIXES = {
    "Music folders": "workspace",
    "FFmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "yt-dlp": "yt_dlp",
    "YouTube helper": "javascript_runtime",
    "rekordbox": "rekordbox",
    "App control": "app_control",
    "rekordbox analysis": "rekordbox_analysis",
    "Shazam history": "shazam",
    "Song recognition": "song_recognition",
    "Claude Code plugin": "claude_plugin",
}


def analysis_hint(result: dict) -> str:
    """BPM and cues come in once `djlib rekordbox sync` has run; after that, by themselves."""
    if not result.get("rekordbox_analysis_folder"):
        return "optional; appears once rekordbox has analyzed tracks"
    if result.get("rekordbox_analysis_synced") is False:
        return "run djlib rekordbox sync once; after that it stays current in the background"
    return "BPM and cues stay current in the background"


def recognition_check(recognition: dict) -> tuple[bool, str, str]:
    """(passed, value, hint) of doctor's song recognition row, for djlib identify."""
    label = str(recognition.get("label") or recognition.get("recognizer") or "")
    if recognition.get("ready"):
        if recognition.get("licensed"):
            return True, f"{label}, licensed", ""
        return (
            True,
            f"{label}, unofficial",
            "may slow down or stop working; AudD or ACRCloud are licensed",
        )
    if not recognition.get("installed"):
        return False, "not installed", "needed to listen to sets (djlib identify)"
    return False, f"{label}: {recognition.get('problem') or 'not set up'}", ""


def shazam_check(shazam: dict) -> tuple[bool, str, str]:
    """(passed, value, hint) of doctor's optional Shazam history row."""
    songs = int(shazam.get("songs") or 0)
    if songs:
        newest = shazam_day(shazam.get("newest_shazamed_at"))
        return True, plural(songs, "song") + (f", newest {newest}" if newest else ""), ""
    # What to do in a few words, in the setup steps' words; the fix under the row is the
    # command (djlib shazam status prints the steps).
    if shazam.get("problem") == "SHAZAM_ACCESS_DENIED":
        value, hint = "no access", "allow the app you run djlib in under Full Disk Access"
    elif shazam.get("problem"):
        value, hint = "can't be read", "for djlib set shazam"
    elif shazam.get("found"):
        # macOS 26 leaves it out of the menu bar until it is added.
        value, hint = "no songs yet", "add Recognize Music to the menu bar and click it once"
    else:
        value, hint = "not set up", "turn on Shazam's iCloud Sync on your iPhone and this Mac"
    return False, value, f"optional; {hint}"


def shazam_day(value: str | None) -> str:
    """ "2026-10-02 21:14" in this Mac's time zone, or ""."""
    from djlib.sources.shazam_history import local_time

    try:
        return local_time(value)
    except ValueError:
        return ""


@view("capabilities")
def capabilities(term: Terminal, result: dict) -> None:
    header(term, "Capabilities", result.get("application_version", ""))
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()
    implemented = [marked(term, "ok", humanize(x)) for x in result.get("implemented") or []]
    planned = [marked(term, "todo", humanize(x)) for x in result.get("planned") or []]
    grid.add_row(Text("Available", style="label"), Text("Planned", style="label"))
    for index in range(max(len(implemented), len(planned))):
        grid.add_row(
            implemented[index] if index < len(implemented) else "",
            planned[index] if index < len(planned) else "",
        )
    term.out.print(Padding(grid, (0, 0, 0, 2)))
    term.out.print()
    note(term, f"Identity: {result.get('identity_method', '')}.")


@view("service status")
def service_status(term: Terminal, result: dict) -> None:
    if result.get("url"):
        status_line(term, "ok", "Background service running", result["url"])
    else:
        status_line(term, "todo", "Background service not running")
        note(term, "It starts automatically when a command needs it.")


@view("service start")
def service_start(term: Terminal, result: dict) -> None:
    status_line(
        term, "ok", "Background service running", f"{result.get('url', '')}  v{__version__}"
    )


@view("service stop")
def service_stop(term: Terminal, result: dict) -> None:
    if result.get("state") == "not_running":
        status_line(term, "todo", "Background service was not running")
    else:
        status_line(term, "ok", "Background service stopped", "accepted jobs stay saved")


@view("upgrade")
def upgrade_view(term: Terminal, result: dict) -> None:
    latest, current = str(result.get("latest") or ""), str(result.get("current") or __version__)
    if result.get("updated") and result.get("repaired") and latest == current:
        status_line(term, "ok", f"djlib {latest} installed again", f"with {result['repaired']}")
    elif result.get("updated"):
        status_line(term, "ok", f"djlib {latest} installed", f"from {current}")
    elif result.get("update_available"):
        status_line(term, "todo", f"djlib {latest} is out", f"you have {current}")
    else:
        status_line(term, "ok", f"djlib {current} is the newest release")
    news = latest != current and result.get("notes")
    missing = result.get("recognition_installed") is False
    fields(
        term,
        [
            ("Release notes", Text(str(result["notes"]), style="path") if news else None),
            ("Song recognition", "not installed" if missing else None),
        ],
    )
    if result.get("update_available") and not result.get("updated"):
        next_steps(term, [("Install it", ("upgrade",))])
    elif result.get("updated"):
        next_steps(term, [("Check your setup", ("doctor",))])
    elif missing and result.get("repair_available"):
        next_steps(term, [("Add song recognition", ("upgrade",))])


@view("setup-agent")
def setup_agent(term: Terminal, result: dict) -> None:
    header(term, "Assistant session ready")
    output = result.get("session_directory") or result.get("output")
    fields(
        term, [("Session", path_text(output)), ("Workspace", path_text(result.get("workspace")))]
    )
    next_steps(
        term,
        [
            (f"Start {host.title()}", " ".join(shell_word(part) for part in command))
            for host in ("claude", "codex")
            if (command := result.get(f"launch_{host}"))
        ],
    )


def shell_word(value: str) -> str:
    """Paths as ``~/…`` outside quotes so the shell still expands them."""
    return shell_path(value) if "/" in value or "\\" in value else quote(value)


@view("demo")
def demo(term: Terminal, result: dict) -> None:
    ingestion = result.get("ingestion") or {}
    export = (result.get("export") or {}).get("result") or {}
    collection_id = (ingestion.get("result") or {}).get("collection_id", "")
    header(term, "Demo complete", "three generated tones, no real music")
    fields(
        term,
        [
            ("Indexed", counts_text(term, ingestion.get("counts"))),
            ("Crate", Text(collection_id)),
            ("Playlist", path_text(export.get("playlist_path"))),
            ("rekordbox XML", path_text(export.get("rekordbox_xml_path"))),
        ],
    )
    next_steps(
        term,
        [
            ("Browse the library", ("library",)),
            ("Open the crate", ("crate", collection_id)),
        ],
    )


@view("plan")
def plan(term: Terminal, result: dict) -> None:
    request = result.get("request") or {}
    header(term, "Collection planned", request.get("name") or "")
    fields(
        term,
        [
            ("Tracks", request.get("track_count")),
            ("Profile", request.get("profile")),
            ("Plan", Text(result.get("plan_id", ""), style="muted")),
            ("Revision", result.get("revision")),
        ],
    )
    next_steps(
        term,
        [
            (
                "Build it",
                ("start", result.get("plan_id", ""), "--revision", str(result.get("revision", 1))),
            )
        ],
    )


@view("usb-preflight")
def usb_preflight(term: Terminal, result: dict) -> None:
    free, total = result.get("free_bytes"), result.get("total_bytes")
    required = result.get("required_bytes") or 0
    header(term, "Storage check", short_path(result.get("path")))
    if isinstance(free, int) and isinstance(total, int) and total:
        width = 30
        filled = round(width * (total - free) / total)
        meter = Text.assemble((term.glyph("bar_full") * filled, "accent"))
        meter.append(term.glyph("bar_empty") * (width - filled), style="muted")
        meter.append(f"  {size(free)} free of {size(total)}")
        term.out.print(Padding(meter, (0, 0, 0, 2)))
    rows: list[tuple[str, object]] = []
    if required:
        enough = result.get("has_requested_space")
        rows.append(
            (
                "Needed",
                Text.assemble(
                    size(required),
                    "  ",
                    marked(term, "ok", "fits")
                    if enough
                    else marked(term, "bad", "not enough space"),
                ),
            )
        )
    if result.get("is_mount_point") is False:
        rows.append(
            ("Volume", Text(f"{term.glyph('warn')} not a mounted drive's top folder", "warn"))
        )
    fields(term, rows)
    term.out.print()
    note(term, "Read-only: nothing was written. Player compatibility is not checked here.")


@view("organize metadata")
def track_metadata(term: Terminal, result: dict) -> None:
    header(
        term, f"{result.get('artist', '')} - {result.get('title', '')}", result.get("version") or ""
    )
    embedded = result.get("embedded") or {}
    effective = result.get("effective") or {}

    def value(entry):
        if isinstance(entry, dict):
            if entry.get("values"):
                return ", ".join(map(str, entry["values"]))
            if entry.get("known") is False:
                return Text("unknown", style="muted")
            return "" if entry.get("value") is None else str(entry["value"])
        if isinstance(entry, list):
            return ", ".join(map(str, entry))
        return "" if entry is None else str(entry)

    rows = [
        (humanize(key), value(embedded.get(key))) for key in ("bpm", "key", "genres", "comments")
    ]
    fields(term, [(label, cell or Text("—", style="muted")) for label, cell in rows])
    extra = [
        (humanize(key), value(effective.get(key)))
        for key in ("tags", "set_role", "energy", "notes")
        if effective.get(key)
    ]
    if extra:
        term.out.print()
        term.out.print(Text("Your notes", style="heading"))
        fields(term, extra)
    term.out.print()
    note(term, "Embedded tags are unverified; djlib does not analyze audio.")


@view("import-rekordbox", "rekordbox pull")
def rekordbox_import(term: Terminal, result: dict) -> None:
    updated, matched = int(result.get("updated") or 0), int(result.get("matched") or 0)
    status_line(
        term,
        "ok" if matched else "warn",
        f"rekordbox analysis imported for {plural(updated, 'track')}"
        if updated
        else "No new analysis to import",
        f"rekordbox {result.get('app_version') or ''}".strip(),
    )
    rows: list[tuple[str, object]] = [
        ("In the XML", plural(int(result.get("tracks_in_xml") or 0), "track")),
        ("Matched", Text(plural(matched, "track"), style="heading" if matched else "")),
    ]
    if result.get("unchanged"):
        rows.append(("Unchanged", str(result["unchanged"])))
    if result.get("kept_your_values"):
        rows.append(("Kept yours", Text(f"{result['kept_your_values']} values you set", "muted")))
    if result.get("not_analyzed"):
        rows.append(("Not analyzed", Text(f"{result['not_analyzed']} without BPM/key", "warn")))
    if result.get("unmatched"):
        rows.append(("Not in catalog", Text(str(result["unmatched"]), style="muted")))
    fields(term, rows)
    if result.get("unmatched_examples"):
        note(term, "e.g. " + "; ".join(result["unmatched_examples"][:3]))
    term.out.print()
    note(term, "Matched by exact file path. BPM/key come from rekordbox and stay unverified.")
    next_steps(term, [("See BPM and key in your library", ("library",))] if updated else [])


@view("rekordbox push")
def rekordbox_push(term: Terminal, result: dict) -> None:
    crates = result.get("crates") or []
    short = [
        crate
        for crate in crates
        if "expected" in crate and int(crate.get("matched") or 0) < int(crate["expected"] or 0)
    ]
    seconds = result.get("rekordbox_ui_seconds")
    status_line(
        term,
        "warn" if short else "ok",
        f"In rekordbox: {plural(len(crates), 'playlist')}"
        + (f", {len(short)} incomplete" if short else ""),
        f"rekordbox needed the screen for {max(1, round(seconds))} s"
        if isinstance(seconds, int | float) and seconds > 0
        else "",
    )
    for crate in crates:
        imported = crate.get("status") == "imported"
        line = Text.assemble((str(crate.get("playlist") or ""), "heading"))
        line.append("  imported" if imported else "  already there", style="muted")
        if "expected" in crate:
            expected = int(crate["expected"] or 0)
            line.append(
                f"  {crate.get('matched', 0)} of {plural(expected, 'track')}",
                style="warn" if crate in short else "heading",
            )
            if crate.get("analyzed") is not None:
                line.append(f", {crate['analyzed']} analyzed", style="muted")
        glyph = "warn" if crate in short else "ok"
        hanging(term.out, Text(term.glyph(glyph), style=glyph), line, indent=2)
        if crate in short:
            gap = int(crate["expected"] or 0) - int(crate.get("matched") or 0)
            why = (
                "rekordbox may have skipped a file it can't read"
                if imported
                else "this playlist may be from an older build of the crate"
            )
            explain = Text(
                f"{plural(gap, 'track')} from the crate {'is' if gap == 1 else 'are'} "
                f"not in this playlist: {why}. Check it in rekordbox before you export it.",
                style="warn",
            )
            term.out.print(indented(explain, 4))
    sync = result.get("analysis_sync") or {}
    if sync:
        term.out.print()
        fields(
            term,
            [("BPM/cues", f"{sync.get('matched', 0)} tracks read from rekordbox's analysis")],
        )
    term.out.print()
    note(
        term,
        "rekordbox keeps analyzing in the background; djlib picks up BPM and cues on its own. "
        "Its database was not edited.",
    )
    ready = [crate for crate in crates if crate not in short and crate.get("collection_id")]
    next_steps(
        term,
        [
            (
                "Put it on your USB"
                if len(crates) == 1
                else f"Put “{crate.get('playlist', '')}” on your USB",
                ("rekordbox", "usb", crate["collection_id"]),
            )
            for crate in ready[:3]
        ],
    )


@view("rekordbox usb")
def rekordbox_usb(term: Terminal, result: dict) -> None:
    expected = int(result.get("expected") or 0)
    failed = usb_failed(result)
    stick = Path(result.get("device") or "").name
    rows: list[tuple[str, object]] = []
    if failed:
        usb_failure(term, result)
        rows.append(("Playlist", Text(f"“{result.get('playlist', '')}”")))
    else:
        seconds = result.get("export_seconds")
        status_line(
            term,
            "ok" if expected else "warn",
            f"“{result.get('playlist', '')}” on {stick}",
            f"exported in {round(seconds)} s" if isinstance(seconds, int | float) else "",
        )
    rows += [
        ("Tracks", Text(usb_tracks(result), style="bad" if failed else "")),
        (
            "Library",
            marked(term, "ok", "rekordbox device library updated")
            if result.get("library_updated")
            else Text("device library unchanged", "warn"),
        ),
        ("Key/BPM", device_analysis(result)),
    ]
    fields(term, rows)
    unmatched(term, result)
    term.out.print()
    note(term, PLAYER_NOTE)
    if failed and result.get("collection_id"):
        next_steps(
            term,
            [("Export it to the stick again", ("rekordbox", "usb", result["collection_id"]))],
        )


PLAYER_NOTE = (
    "Exported by rekordbox; djlib only read the stick. Test it on your player before a gig."
)


def usb_failed(usb: dict) -> bool:
    """A stick that came back short or out of order must not leave for the gig."""
    expected = int(usb.get("expected") or 0)
    return bool(expected) and (
        int(usb.get("found") or 0) != expected or usb.get("in_order") is False
    )


def usb_problem(usb: dict) -> str:
    """``1 of 8 missing``, ``tracks out of order`` or both."""
    found, expected = int(usb.get("found") or 0), int(usb.get("expected") or 0)
    problems = []
    if found < expected:
        problems.append(f"{expected - found} of {expected} missing")
    elif found != expected:
        problems.append(f"{found} found, {expected} expected")
    if usb.get("in_order") is False:
        problems.append("out of order" if problems else "tracks out of order")
    return " and ".join(problems)


def usb_failure(term: Terminal, usb: dict) -> None:
    stick = Path(usb.get("device") or "").name or "the USB"
    dash = "—" if term.unicode else "-"
    status_line(
        term,
        "bad",
        f"USB check failed: {usb_problem(usb)} on {stick} {dash} don't take this stick yet",
    )


def usb_tracks(usb: dict) -> str:
    found, expected = int(usb.get("found") or 0), int(usb.get("expected") or 0)
    if not usb.get("playlist_on_device"):
        return f"{found} of {expected} files on the USB, byte for byte (playlist not read)"
    if usb.get("in_order") is False:
        return f"{found} of {expected} in the player's library, byte for byte, but out of order"
    order = ", in order and" if usb.get("in_order") else ","
    return f"{found} of {expected} in the player's library{order} byte for byte"


def unmatched(term: Terminal, usb: dict) -> None:
    """The crate's tracks the stick did not give back."""
    missing = usb.get("missing") or []
    if not missing:
        return
    count = max(len(missing), int(usb.get("expected") or 0) - int(usb.get("found") or 0))
    term.out.print()
    term.out.print(Text(f"Not found on the stick ({count})", style="heading"))
    for label in missing:
        hanging(term.out, Text(term.glyph("bad"), style="bad"), Text(str(label)), indent=2)
    if count > len(missing):
        note(term, f"… and {count - len(missing)} more")


def device_analysis(result: dict) -> Text | None:
    """“Key/BPM 5 tracks read from the stick (2 new)” after a verified USB export."""
    analysis = result.get("analysis_from_device") or {}
    if not analysis.get("matched"):
        return None
    line = f"{plural(int(analysis['matched']), 'track')} read from the stick"
    if analysis.get("updated"):
        line += f" ({analysis['updated']} new in your catalog)"
    return Text(line, "muted")


def fetched_line(term: Terminal, fetched: dict) -> Text:
    chosen = len(fetched.get("chosen") or [])
    downloaded, failed = int(fetched.get("downloaded") or 0), int(fetched.get("failed") or 0)
    if downloaded:
        line = marked(term, "ok", f"{plural(downloaded, 'song')} downloaded as MP3")
        if failed:
            line.append(f", {failed} failed", style="warn")
        return line
    if chosen:
        return Text(f"{plural(chosen, 'song')} found; not downloaded (rerun with --yes)", "warn")
    return Text("no missing song had a clear match", "muted")


# Above this many songs (a Shazam history), the table lists only the songs you don't own.
BIG_SET = 60
BIG_ROWS = 25
OWN_WIDTH = 12  # "≈ Radio Edit"; longer mix names wrap under it
# How sure djlib is of a line worth a listen before it's played; an ID is just an ID.
UNSURE = frozenset({"likely", "weak", "conflict"})
NARROW = 100  # below this many columns, the set and identify tables save room where they can


@view("set")
def set_view(term: Terminal, result: dict) -> None:
    owned = int(result.get("owned") or 0)
    tracks = [track for track in result.get("tracks") or [] if isinstance(track, dict)]
    usb = result.get("usb") or {}
    usb_bad = bool(usb) and usb_failed(usb)
    tally = set_tally(term, result, tracks)
    rows: list[tuple[str, object]] = []
    if usb_bad:
        # The stick is what leaves for the gig; its failure leads.
        usb_failure(term, usb)
        rows.append(("Set", Text(result.get("name") or "").append("  ").append_text(tally)))
    else:
        tone = "ok" if owned and (not usb or usb.get("expected")) else "warn"
        line = Text(result.get("name") or "Set", style="heading")
        # The counts stay whole: beside the name when they fit, else on the line under it.
        beside = 2 + line.cell_len + 2 + tally.cell_len <= term.out.width
        line.append("  " if beside else "\n").append_text(tally)
        hanging(term.out, Text(term.glyph(tone), style=TONE_STYLES[tone]), line)
        flush_warnings(term)
    rekordbox = result.get("rekordbox") or {}
    page = result.get("source") or {}
    if page:
        provider = provider_name(page.get("provider"))
        rows.append(
            ("Source", Text(f"{provider}: {page.get('title') or page.get('url')}", "muted"))
        )
    identified = result.get("identified") or {}
    if identified:
        rows.append(("Identified", identified_line(term, identified)))
        rows.append(("Tracklist", path_text(identified.get("tracklist_file"))))
    fetched = result.get("fetched") or {}
    if fetched:
        rows.append(("Fetched", fetched_line(term, fetched)))
    if result.get("collection_id"):
        size = int(result.get("crate_tracks") or owned)
        rows.append(("Crate", marked(term, "ok", f"{plural(size, 'track')}, in set order")))
        if rekordbox.get("reason_code") == "NOT_REQUESTED":
            rows.append(("rekordbox", Text("not added yet (--no-rekordbox)", "muted")))
        elif rekordbox.get("status") == "skipped":
            # A skip the user can fix (locked screen, permission) is a warning, not a footnote.
            fixable = rekordbox.get("reason_code") in {
                "APP_SCREEN_LOCKED",
                "APP_AUTOMATION_NOT_ALLOWED",
            }
            reason = f"skipped: {rekordbox.get('reason')}"
            rows.append(
                ("rekordbox", marked(term, "warn", reason) if fixable else Text(reason, "muted"))
            )
        else:
            there = "imported" if rekordbox.get("status") == "imported" else "already there"
            playlist = f"playlist “{result.get('playlist')}” {there}"
            if rekordbox.get("replaces"):
                # The set changed: a new version beside the playlist djlib made earlier.
                playlist += f"; the older “{rekordbox['replaces']}” is still there to delete"
            rows.append(("rekordbox", marked(term, "ok", playlist)))
    elif waiting(tracks):
        rows.append(("Crate", Text("none yet: the songs marked ! need a look first", "warn")))
    elif any(track.get("own") == "other_mix" for track in tracks):
        rows.append(("Crate", Text("no exact mixes in your library yet", "warn")))
    else:
        rows.append(("Crate", Text("none of these songs are in your library yet", "warn")))
    if usb:
        stick = Path(usb.get("device") or "").name
        glyph = "bad" if usb_bad else "ok" if usb.get("expected") else "warn"
        rows.append(("USB", marked(term, glyph, f"{stick}: {usb_tracks(usb)}")))
        rows.append(("Key/BPM", device_analysis(usb)))
    fields(term, rows)
    mixesdb_source(term, result.get("mixesdb"))
    if usb_bad:
        unmatched(term, usb)
    unsure = set_table(term, result, tracks)
    pick = fetched.get("needs_your_pick") or []
    if pick:
        term.out.print()
        term.out.print(Text(f"Not downloaded: no clear match ({len(pick)})", style="heading"))
        for entry in pick[:10]:
            line = Text.assemble((str(entry.get("label") or ""), "heading"))
            options = entry.get("options") or []
            if not options:
                line.append(f"  {entry.get('reason') or 'nothing found'}", style="muted")
                term.out.print(indented(line))
                continue
            line.append("  best guess:", style="muted")
            term.out.print(indented(line))
            # One unbroken line, so the link stays clickable and copyable.
            url = Text.assemble("    ", (str(options[0].get("url") or ""), "path"))
            term.out.print(url, soft_wrap=True)
    hints = result.get("id_hints") or []
    if hints:
        links = {track.get("position"): track.get("listen_url") for track in tracks}
        term.out.print()
        term.out.print(Text("IDs: what listeners named around that moment", style="heading"))
        for hint in hints:
            best = hint["hints"][0]
            line = Text.assemble((str(best.get("label") or ""), "heading"))
            line.append(f"  {plural(int(best.get('mentions') or 1), 'mention')}", style="muted")
            others = [str(h.get("label") or "") for h in hint["hints"][1:]]
            if others:
                line.append(f"  {term.glyph('dot')} also: {', '.join(others)}", style="muted")
            moment = Text(f"#{hint.get('position')} @ ", style="muted")
            moment.append_text(set_time(hint.get("timestamp"), links.get(hint.get("position"))))
            hanging(term.out, moment.append(" "), line, indent=2)
    caveats = set_notes(
        downloaded=bool(fetched.get("downloaded")),
        hints=bool(hints),
        usb=bool(usb),
        unsure=unsure,
        other_mix=sum(1 for track in tracks if track.get("own") == "other_mix"),
    )
    if caveats:
        term.out.print()
    for line in caveats:
        note(term, line)
    steps: list[Step] = []
    # Owned, but not in the crate until the DJ picks a copy or the file is back.
    states = Counter(track.get("state") for track in waiting(tracks))
    if states["ambiguous"]:
        steps.append((f"Pick which copy to use ({states['ambiguous']})", ("ui",)))
    if states["unavailable"]:
        refresh = ("requests", "refresh", result.get("request_id", ""))
        steps.append(("Once the unavailable files are back, check again", refresh))
    crate = result.get("collection_id")
    if usb_bad and crate:
        steps.append(("Export it to the stick again", ("rekordbox", "usb", crate)))
    elif crate and rekordbox.get("reason_code") in {
        "APP_SCREEN_LOCKED",
        "APP_AUTOMATION_NOT_ALLOWED",
    }:
        steps.append(("Put it in rekordbox once that's sorted", ("rekordbox", "push", crate)))
    elif crate and rekordbox.get("reason_code") == "NOT_REQUESTED" and rekordbox.get("installed"):
        # Checked first with --no-rekordbox; the crate is ready for rekordbox when the DJ is.
        steps.append(("Put it in rekordbox", ("rekordbox", "push", crate)))
        steps.append(("Or straight onto your USB", ("rekordbox", "usb", crate)))
    elif crate and rekordbox.get("status") == "skipped":
        steps.append(("See the crate", ("crate", crate)))
    elif crate and not usb:
        steps.append(("Put it on your USB", ("rekordbox", "usb", crate)))
    maybe = sum(1 for track in tracks if track.get("own") == "maybe")
    if maybe:
        # Owned only once the DJ says the file is the song: Use this on the review page.
        steps.append((f"Check the songs that may be yours ({maybe})", ("ui",)))
    to_buy = sum(1 for track in tracks if track.get("own") == "missing")
    if to_buy:
        report = ("requests", "report", result.get("request_id", ""))
        steps.append((f"Your shopping list ({to_buy})", report))
    if result.get("listen_fix"):
        steps.append(("So djlib can listen to sets, fix this", result["listen_fix"]))
    if result.get("shazam"):
        from djlib.sources.shazam_history import from_this_mac

        # shazam:new reads this Mac's history only, never a file or Apple Music. After a list
        # cut at 1,000 songs, next_spec brings the next ones.
        if from_this_mac(result["shazam"]):
            left = int(result["shazam"].get("remaining") or 0)
            if left:
                spec = result["shazam"].get("next_spec") or "shazam:new"
                steps.append((f"The next {shazam_count(left, 'Shazam')}", ("set", spec)))
            else:
                steps.append(("Later, check only what you Shazam next", ("set", "shazam:new")))
    next_steps(term, steps)


def set_tally(term: Terminal, result: dict, tracks: list[dict]) -> Text:
    """ "10 of 22 owned · 2 to check · 1 in another mix · 7 missing · 2 IDs", each part in its
    colour; "owned" counts the crate, and an owned song waiting on a pick is "to check"."""
    owned, songs = int(result.get("owned") or 0), int(result.get("songs") or 0)
    own = Counter(track.get("own") for track in tracks)
    text = Text(f"{owned} of {songs} owned", style="heading")
    for amount, label, style in (
        (len(waiting(tracks)), "to check", "warn"),
        (own["maybe"], "maybe yours", "warn"),
        (own["other_mix"], "in another mix", "warn"),
        (own["missing"], "missing", "bad"),
        (own["id"], "ID" if own["id"] == 1 else "IDs", "muted"),
    ):
        if amount:
            text.append(f" {term.glyph('dot')} ", style="muted")
            text.append(f"{amount} {label}", style=style)
    return text


def set_table(term: Terminal, result: dict, tracks: list[dict]) -> bool:
    """The whole set in set order: time, track, how sure djlib is, and what you own. A long
    list (a Shazam history) shows the first songs not in the crate. True when a likely, weak or
    conflicting line is worth a listen; why each is there is said under the table."""
    if not tracks:
        return False
    shown = tracks
    heading = f"{'Your Shazams' if result.get('shazam') else 'The set'} ({len(tracks)})"
    big = len(tracks) > BIG_SET
    if big:
        # A copy to pick or a file to find keeps an owned song out of the crate too.
        shown = [track for track in tracks if track.get("state") != "satisfied"]
        if shown:
            owned = any(track.get("own") == "owned" for track in shown)
            title = "Not in the crate yet" if owned else "Not owned yet"
            heading = f"{title} ({len(shown)} of {len(tracks)})"
    term.out.print()
    term.out.print(Text(heading, style="heading"))
    more = len(shown) - BIG_ROWS if big else 0
    shown = shown[:BIG_ROWS] if more > 0 else shown
    if not shown:
        note(term, "You own every one of them.")
        return False
    # A pasted tracklist times only its IDs; their time then goes beside them, not in a column.
    timed = any(track.get("timestamp") for track in shown if track.get("own") != "id")
    sure = any(track.get("confidence") for track in shown)
    columns: list[tuple[str, dict]] = [("#", {"justify": "right", "style": "muted"})]
    if timed:
        columns.append(("Time", {}))
    # The name never loses its mix: it wraps instead of being cut short.
    columns.append(("Track", {"overflow": "fold", "wrap": True, "readable": TITLE_WIDTH}))
    if sure:
        columns.append(("Sure", {}))
    # Only the mix names wrap; "! owned, pick a copy" keeps to one line.
    readable = max([OWN_WIDTH, *(own_cell(term, track).cell_len for track in waiting(shown))])
    own = {"overflow": "fold", "wrap": True, "readable": readable, "shrinks_first": True}
    columns.append(("Own", own))
    grid = table(*columns, gutter=2)
    for track in shown:
        cells: list[object] = [str(track.get("position") or "")]
        if timed:
            cells.append(set_time(track.get("timestamp"), track.get("listen_url")))
        cells.append(set_track_cell(term, track, timed=timed))
        if sure:
            cells.append(sure_cell(term, track.get("confidence")))
        cells.append(own_cell(term, track))
        grid.add_row(*cells)
    term.out.print(grid)
    if more > 0:
        # One unbroken line, so the command pastes whole.
        line = Text(f"  {'…' if term.unicode else '...'} and {more} more: ", style="muted")
        line.append(
            term.command("requests", "report", short_handle(result.get("request_id") or "")),
            style="cmd",
        )
        term.out.print(line, soft_wrap=True)
    spellings(term, shown)
    maybe_yours(term, shown)
    reasons(
        term,
        [
            (track.get("position"), track.get("notes"))
            for track in shown
            if track.get("confidence") not in {None, "confirmed"}
        ],
    )
    return any(track.get("confidence") in UNSURE for track in shown)


def spellings(term: Terminal, tracks: list[dict]) -> None:
    """ "Owned under another spelling: #2 Robert Glasper": the tracklist's credits a file
    matched where Shazam's are shown."""
    found = [
        f"#{track.get('position')} {credit}"
        for track in tracks
        if track.get("own") == "owned" and (credit := owned_as(track))
    ]
    if found:
        term.out.print()
        note(term, "Owned under another spelling: " + f" {term.glyph('dot')} ".join(found))


def maybe_yours(term: Terminal, tracks: list[dict]) -> None:
    """ "Maybe yours under another name: #2 Robert Glasper - Move Love (Kaytranada Edition)":
    a file with the same title and mix whose artist credit is shorter or longer. Not owned
    until the DJ says so (Use this on the review page)."""
    found = [
        f"#{track.get('position')} {track['maybe_owned']}"
        for track in tracks
        if track.get("own") == "maybe" and track.get("maybe_owned")
    ]
    if found:
        term.out.print()
        note(term, "Maybe yours under another name: " + f" {term.glyph('dot')} ".join(found))


def set_time(clock: str | None, url: str | None) -> Text:
    """A time in the set; where the terminal can, a link that plays the set from there."""
    return Text(clock or "", style=Style(link=url) if clock and url else "")


def set_track_cell(term: Terminal, track: dict, *, timed: bool = True) -> Text:
    """The song as listed, or "ID — maybe X" in grey: a guess is never the ID's name. Without
    a Time column, an ID keeps its time beside it ("ID @ 06:20")."""
    if track.get("own") != "id":
        return track_label(track)
    label = str(track.get("label") or "ID")
    text = Text(label, style="muted")
    if not timed and track.get("timestamp"):
        text.append(" @ ", style="muted")
        text.append_text(set_time(track["timestamp"], track.get("listen_url")))
        text.stylize("muted")
    if track.get("maybe"):
        word = "likely" if track.get("confidence") == "likely" else "maybe"
        text.append(f" {'—' if term.unicode else '-'} {word} {track['maybe']}", style="muted")
    return text


def sure_cell(term: Terminal, confidence: str | None) -> Text:
    """ "◐ likely", "! weak"; on a narrow screen a confirmed line is just "✓", so the names
    keep the room and the lines worth a listen stand out."""
    if not confidence:
        return Text("")
    if confidence == "confirmed" and term.out.width < NARROW:
        return Text(term.glyph("ok"), style=TONE_STYLES["ok"])
    glyph, tone, label = TRACK_STATUSES.get(confidence, ("todo", "todo", confidence))
    return marked(term, glyph, label, tone)


def own_cell(term: Terminal, track: dict) -> Text:
    """✓ owned, ✗ missing, ≈ Radio Edit (the other mix you own); blank for IDs. The spelling a
    file matched under goes under the table (``spellings``), so the column stays narrow."""
    own, state = track.get("own"), track.get("state")
    if own == "owned" and state == "ambiguous":
        return marked(term, "warn", "owned, pick a copy")
    if own == "owned" and state == "unavailable":
        return marked(term, "warn", "owned, file unavailable")
    if own == "owned":
        return marked(term, "ok", "owned")
    if own == "other_mix":
        mixes = owned_mixes(track.get("you_own") or [], track.get("title"))
        return marked(term, "other", mixes, "warn")
    if own == "maybe":
        return marked(term, "maybe", "maybe yours", "warn")
    if own == "missing":
        return marked(term, "bad", "missing")
    return Text("")


def waiting(tracks: list[dict]) -> list[dict]:
    """Songs you own in the exact mix that aren't in the crate: a copy to pick, or a file
    djlib couldn't read."""
    return [
        track
        for track in tracks
        if track.get("own") == "owned" and track.get("state") not in {None, "satisfied"}
    ]


def owned_mixes(mixes: list, title: str | None) -> str:
    """ "Radio Edit, Clean": the mixes you own; a copy with no mix name says so, rather than
    repeating the song's title."""
    plain = str(title or "").strip().casefold()
    names = ["no mix name" if str(mix).strip().casefold() == plain else str(mix) for mix in mixes]
    return ", ".join(dict.fromkeys(names))


def owned_as(track: dict) -> str:
    """The credit a song matched under when it isn't the listed one ("Robert Glasper"), or the
    whole label when more than the artist differs."""
    matched = str(track.get("matched_as") or "")
    if not matched or matched == track.get("label"):
        return ""
    artist, _, rest = matched.partition(" - ")
    listed = str(track.get("label") or "").partition(" - ")[2]
    return artist if rest and rest == listed else matched


def mixesdb_source(term: Terminal, page: dict | None) -> None:
    """ "Tracklist from MixesDB: <page title>" and the page's link, so the DJ can check it."""
    if not page:
        return
    line = Text.assemble("Tracklist from MixesDB: ", (str(page.get("page_title") or ""), "heading"))
    if page.get("tracklist") == "incomplete":
        line.append("  marked incomplete", style="warn")
    term.out.print(indented(line))
    # One unbroken line, so the link stays clickable and copyable.
    term.out.print(Text("  " + str(page.get("page_url") or ""), style="path"), soft_wrap=True)


def identified_line(term: Terminal, identified: dict) -> Text:
    """ "9 heard by Shazam · 13 from tracklists only  listened 14 Oct" for `djlib set`."""
    line = Text(
        heard_tally(term, identified.get("counts") or {}, identified.get("recognizer"), whole=False)
        or "tracklist made"
    )
    if day := listened_day(identified.get("listened_at")):
        line.append(f"  listened {day}", style="muted")
    return line


def listened_day(value: str | None) -> str:
    """ "14 Oct" in this computer's time zone (with the year when it isn't this one), or ""."""
    moment = parse_time(value)
    if moment is None:
        return ""
    local = moment.astimezone()
    day = f"{local.day} {local:%b}"
    return day if local.year == datetime.now().year else f"{day} {local.year}"


def set_notes(
    *, downloaded: bool, hints: bool, usb: bool, unsure: bool = False, other_mix: int = 0
) -> list[str]:
    """Short lines of caveats under a set, only the ones that apply."""
    parts = [
        part
        for part, shown in (
            ("Likely and weak lines are worth a listen before you play them.", unsure),
            (
                f"You own another mix of {plural(other_mix, 'song')}; "
                "djlib only counts the exact mix.",
                other_mix,
            ),
            ("Downloads: unverified web audio.", downloaded),
            ("ID hints are listeners' guesses.", hints),
            ("Test the stick on your player before a gig.", usb),
        )
        if shown
    ]
    lines: list[str] = []
    for part in parts:
        if lines and len(lines[-1]) + 1 + len(part) <= 78:
            lines[-1] += " " + part
        else:
            lines.append(part)
    return lines


# -- shazam ------------------------------------------------------------------------------------


def shazam_count(count: int, word: str = "song") -> str:
    """ "1,520 songs": a Shazam history runs into the thousands."""
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def shazam_source_line(term: Terminal, source: dict) -> None:
    """``✓ Music Recognition on this Mac  412 songs, newest 2026-10-02 21:14``."""
    problem = source.get("problem") or {}
    count = len(source.get("backups") or [])
    passed = bool(source.get("songs") or count)
    if problem.get("code") == "SHAZAM_ACCESS_DENIED":
        detail = "no access (needs Full Disk Access)"
    elif problem:
        detail = "can't be read"
    elif source.get("source") == "iphone_backup":
        detail = plural(count, "backup")
    elif passed:
        newest = shazam_day(source.get("newest_shazamed_at"))
        detail = shazam_count(int(source["songs"])) + (f", newest {newest}" if newest else "")
    else:
        detail = "no songs yet" if source.get("found") else "not found"
    line = Text(str(source.get("label") or ""))
    line.append(f"  {detail}", style="" if passed else "muted")
    glyph = Text(term.glyph("ok" if passed else "todo"), style="ok" if passed else "muted")
    hanging(term.out, glyph, line, indent=2)


SHORT_HISTORY = 100  # songs one look can take in; past this, start with the last 30 days


def first_check(songs: int, newest: str | None) -> list[Step]:
    """How to check a history never imported. `djlib set shazam` takes the oldest 1,000, so a
    long history starts with the last 30 days: what a DJ Shazamed lately."""
    from djlib.sources.shazam_history import RECENT_DAYS, day_range, recent_since

    if songs <= SHORT_HISTORY:
        return [("Check them against your music", ("set", "shazam"))]
    since, ends_today = recent_since(newest)
    # Nothing Shazamed lately: name the days, since "last month" would read as the calendar's.
    recent = (
        f"Check the last {RECENT_DAYS} days"
        if ends_today or not newest
        else f"Check your last month of Shazams ({day_range(since, newest)})"
    )
    return [
        (recent, ("set", f"shazam:{since}")),
        ("Or your whole history, oldest first", ("set", "shazam")),
    ]


@view("shazam status")
def shazam_status(term: Terminal, result: dict) -> None:
    from djlib.sources.shazam_history import FULL_DISK_APPS, FULL_DISK_SETTINGS, day

    songs = int(result.get("songs") or 0)
    # The history is there but macOS won't open it: say that, not "set up iCloud".
    locked = bool(result.get("full_disk_access")) and not songs
    if songs:
        newest = shazam_day(result.get("newest_shazamed_at"))
        status_line(
            term,
            "ok",
            "Shazam history",
            shazam_count(songs) + (f", newest {newest}" if newest else ""),
        )
    elif locked:
        status_line(term, "bad", "djlib can't open your Shazam history yet")
        line = f"Allow {FULL_DISK_APPS} under Full Disk Access, then quit and reopen it."
        term.out.print(indented(Text(line)))
    else:
        status_line(term, "todo", "No Shazam history on this Mac yet")
    for source in result.get("sources") or []:
        # Where djlib looked; iPhone backups only when there is one to read.
        if not locked and (source.get("source") != "iphone_backup" or source.get("backups")):
            shazam_source_line(term, source)
    last = result.get("last_import") or {}
    if last:
        imported = Text(shazam_day(last.get("imported_at")))
        imported.append(
            f"  “{last.get('name') or 'Shazam'}”, {shazam_count(int(last.get('songs') or 0))}",
            style="muted",
        )
        fresh = result.get("new_since_last_import")
        term.out.print()
        fields(
            term,
            [
                ("Last import", imported),
                ("New since", None if fresh is None else shazam_count(int(fresh))),
            ],
        )
    if result.get("full_disk_access") and not locked:
        term.out.print()
        note(term, result["full_disk_access"])
    steps: list[Step] = []
    setup = result.get("setup") or []
    if result.get("full_disk_access"):
        steps.append(("Open Full Disk Access", FULL_DISK_SETTINGS))
    if setup:
        term.out.print()
        term.out.print(Text("Turn on Shazam sync", style="heading"))
        for number, step in enumerate(setup, 1):
            hanging(term.out, Text(f"{number}.", style="muted"), Text(step), indent=2)
        backups = next(
            (s.get("backups") for s in result.get("sources") or [] if s.get("backups")), None
        )
        # Ways around iCloud: the first says "instead", the others start with "Or".
        music = "Or Apple Music's “My Shazam Tracks”"
        if backups:
            steps.append(("Read your iPhone backup instead", ("set", f"shazam:{backups[0]}")))
        else:
            music = "Use Apple Music's “My Shazam Tracks” instead"
        steps += [
            (music, ("set", "shazam:apple-music")),
            (
                "Or a CSV from shazam.com/myshazam (Download CSV, if your account shows it)",
                # shazam:FILE, like the steps above: it reads any CSV as Shazams.
                ("set", f"shazam:{Path.home() / 'Downloads' / 'shazamlibrary.csv'}"),
            ),
        ]
    elif last:
        fresh = result.get("new_since_last_import")
        # The rest of a list cut at 1,000 that shazam:new can't bring, until it is checked.
        rest = (last.get("pending") or {}) if songs else {}
        newest = last.get("newest_list") or last  # the list with your newest Shazams
        if fresh:
            steps.append(("Check the new ones against your music", ("set", "shazam:new")))
        if rest.get("spec") and int(rest.get("songs") or 0):
            label = f"The next {shazam_count(int(rest['songs']), 'Shazam')}"
            if rest.get("since"):
                label += f", from {day(rest['since'])}"
            steps.append((label, ("set", rest["spec"])))
        elif fresh == 0 and newest.get("collection_id"):
            steps.append(("Your last Shazam crate", ("crate", newest["collection_id"])))
    elif songs:
        steps += first_check(songs, result.get("newest_shazamed_at"))
    if songs:
        steps.append(("See them", ("shazam", "list")))
    next_steps(term, steps)


@view("shazam list")
def shazam_list(term: Terminal, result: dict) -> None:
    from djlib.sources.shazam_history import place_name

    records = result.get("records") or []
    songs = int(result.get("songs") or 0)
    since = shazam_day(result.get("since"))
    where = " + ".join(str(s.get("label") or "") for s in result.get("sources") or [])
    if not records:
        label = "Nothing new Shazamed" if result.get("new") else "No Shazams"
        status_line(term, "todo", label, f"since {since}" if since else where)
        return
    subtitle = shazam_count(songs) + (f" since {since}" if since else "")
    header(term, "Your Shazams", f"{subtitle}  {where}".strip())
    grid = table(
        ("Shazamed", {"style": "muted"}),
        ("Artist", {"overflow": "ellipsis", "readable": TITLE_WIDTH}),
        ("Title", {"overflow": "ellipsis", "readable": TITLE_WIDTH}),
        ("×", {"justify": "right", "drop": 2}),
        ("Where", {"overflow": "ellipsis", "yields": True, "drop": 1}),
    )
    for item in records:
        count = int(item.get("count") or 1)
        grid.add_row(
            # The latest Shazam: the list is newest first by it.
            shazam_day(item.get("last_shazamed_at") or item.get("shazamed_at")) or "?",
            item.get("artist") or "",
            item.get("title") or "",
            f"{count}×" if count > 1 else "",
            # A place's name helps; bare coordinates don't.
            place_name(item.get("location")),
        )
    term.out.print(grid)
    if len(records) < songs:
        more = ["shazam", "list", "--limit", str(min(songs, 1000))]
        query = result.get("query") or {}
        for option in ("since", "from"):
            if query.get(option):
                more[2:2] = [f"--{option}", str(query[option])]
        more_line(term, f"{len(records)} of {shazam_count(songs)}, newest first", tuple(more))
    if result.get("spec") == "shazam":
        next_steps(term, first_check(songs, result.get("newest_shazamed_at")))
    elif result.get("spec"):  # none for a file or Apple Music with --since (see the warning)
        next_steps(term, [("Check them against your music", ("set", result["spec"]))])


# -- identify ----------------------------------------------------------------------------------

# Track status → (glyph, tone, label): how sure djlib is of each line of a tracklist.
TRACK_STATUSES = {
    "confirmed": ("ok", "ok", "confirmed"),
    "likely": ("run", "info", "likely"),
    "weak": ("warn", "warn", "weak"),
    "conflict": ("bad", "bad", "conflict"),
    "unidentified": ("todo", "todo", "ID"),
}


def heard_tally(
    term: Terminal, counts: dict, recognizer: dict | None = None, *, whole: bool = True
) -> str:
    """ "22 tracks · 9 heard by Shazam · 13 from tracklists only · 2 IDs": how djlib knows each
    song. ``whole`` False leaves out the totals a set's headline already gives."""
    dot = f" {term.glyph('dot')} "
    parts = [plural(int(counts.get("tracks") or 0), "track")] if whole else []
    if "heard" not in counts:
        # Counted before djlib kept these: how sure it is of each line instead.
        parts += [f"{counts[s]} {TRACK_STATUSES[s][2]}" for s in TRACK_STATUSES if counts.get(s)]
        return dot.join(parts)
    name = recognizer_name(str((recognizer or {}).get("name") or "")) or "song recognition"
    if counts.get("heard"):
        parts.append(f"{counts['heard']} heard by {name}")
    if counts.get("listed_only"):
        parts.append(f"{counts['listed_only']} from tracklists only")
    if whole and counts.get("ids"):
        parts.append(plural(int(counts["ids"]), "ID"))
    return dot.join(parts)


def provider_name(value: str | None) -> str:
    """ "YouTube" for yt-dlp's "Youtube" (or "YoutubeTab"), "SoundCloud" for "Soundcloud"."""
    name = (value or "").removesuffix("Tab")
    return {"Youtube": "YouTube", "Soundcloud": "SoundCloud"}.get(name, name)


def identified_track(term: Terminal, track: dict) -> Text:
    """A named track, or "ID — likely X": a candidate is never shown as the ID's name."""
    if track.get("kind") != "unknown":
        return track_label(track)
    text = Text("ID", style="heading")
    candidates = track.get("candidates") or []
    if candidates:
        best = candidates[0]
        word = "likely" if track.get("status") == "likely" else "maybe"
        if best.get("source") == "comments":
            word = "listeners say"
        dash = "—" if term.unicode else "-"
        text.append(f" {dash} {word} {best.get('label') or ''}", style="muted")
    return text


def recognizer_name(name: str) -> str:
    return {"shazam": "Shazam", "audd": "AudD", "acrcloud": "ACRCloud"}.get(name, name)


def reasons(term: Terminal, rows: list[tuple[object, object]]) -> None:
    """ "#3  Listed as an ID" under a table, for the lines worth a listen; lines sharing a
    reason share a line ("From MixesDB: #3, #5, #8"), not one each."""
    grouped: dict[str, list[object]] = {}
    for n, why in rows:
        if why:
            grouped.setdefault(str(why), []).append(n)
    if not grouped:
        return
    term.out.print()
    for why, numbers in grouped.items():
        if len(numbers) == 1:
            lead = Text(f"#{numbers[0]}", style="muted")
            hanging(term.out, lead, Text(why, style="muted"), indent=2)
        else:
            listed = ", ".join(f"#{n}" for n in numbers)
            term.out.print(indented(Text(f"{why}: {listed}", style="muted"), 2))


def unofficial_notice(result: dict) -> bool:
    """Whether this listen is the one that says, once per workspace, that Shazam is unofficial."""
    from djlib.application.identification import UNOFFICIAL_SHAZAM

    return UNOFFICIAL_SHAZAM in (result.get("notices") or [])


@view("identify")
def identify_view(term: Terminal, result: dict) -> None:
    source = result.get("source") or {}
    subtitle = " · ".join(
        part for part in (provider_name(source.get("provider")), result.get("duration")) if part
    )
    header(term, source.get("title") or source.get("url") or "Set", subtitle)
    tracks = result.get("tracks") or []
    counts = {"tracks": len(tracks), **(result.get("counts") or {})}
    recognizer = result.get("recognizer") or {}
    heard_by = None
    if recognizer.get("name"):
        heard_by = Text(recognizer_name(recognizer["name"]))
        if day := listened_day(result.get("listened_at")):
            heard_by.append(f" {term.glyph('dot')} listened {day}")
        if not recognizer.get("licensed") and unofficial_notice(result):
            heard_by.append("  unofficial; may slow down or stop working", style="muted")
    fields(
        term,
        [
            ("Tracklist", Text(heard_tally(term, counts, recognizer))),
            ("Heard by", heard_by),
            ("Saved to", path_text(result.get("tracklist_file"))),
        ],
    )
    mixesdb_source(term, result.get("mixesdb"))
    unsure = [(t.get("n"), t.get("evidence")) for t in tracks if t.get("status") != "confirmed"]
    if tracks:
        term.out.print()
        grid = table(
            ("#", {"justify": "right", "style": "muted"}),
            ("Time", {}),
            # The name never loses its mix: it wraps instead of being cut short.
            ("Track", {"overflow": "fold", "wrap": True, "readable": TITLE_WIDTH}),
            ("Sure", {}),
            gutter=2,
        )
        for track in tracks:
            start = track.get("start") or ""
            grid.add_row(
                str(track.get("n") or ""),
                set_time(
                    f"~{start}" if track.get("approx") and start else start, track.get("listen_url")
                ),
                identified_track(term, track),
                sure_cell(term, track.get("status")),
            )
        term.out.print(grid)
        # Why each line worth a listen is there, under the table at any width.
        reasons(term, unsure)
    unplaced = result.get("unplaced") or []
    if unplaced:
        # Songs heard in one window only: one a line, at the moment to listen to.
        term.out.print()
        more = f" (and {len(unplaced) - 5} more)" if len(unplaced) > 5 else ""
        note(term, f"Heard once, not placed{more}")
        width = max(cell_len(str(song.get("at") or "?")) for song in unplaced[:5])
        for song in unplaced[:5]:
            at = str(song.get("at") or "?")
            line = Text(" " * (width - cell_len(at)), style="muted")
            line.append_text(set_time(at, song.get("listen_url")))
            line.append(f"  {song.get('label') or ''}")
            term.out.print(indented(line, 4))
    if any(track.get("status") in UNSURE for track in tracks):
        term.out.print()
        note(term, "Likely and weak lines are worth a listen before you play them.")
    url = source.get("url") or "LINK"
    steps: list[Step] = [
        ("Check it against your music", ("set", url, "--no-rekordbox")),
        ("Put it in rekordbox", ("set", url)),
    ]
    if any("--tracklist" in step for step in result.get("next") or []):
        tracklist = ("identify", url, "--tracklist", "paste")
        steps.append(("Copy its tracklist from 1001Tracklists, then", tracklist))
    next_steps(term, steps)


@view("rekordbox sync")
def rekordbox_sync(term: Terminal, result: dict) -> None:
    updated = int(result.get("updated") or 0)
    status_line(
        term,
        "ok",
        f"rekordbox analysis: {plural(int(result.get('matched') or 0), 'track')} matched",
        f"{updated} updated · {result.get('changed_files', 0)} changed files read",
    )
    rows: list[tuple[str, object]] = []
    if result.get("kept_your_values"):
        rows.append(("Kept yours", f"{result['kept_your_values']} BPM values you set"))
    if result.get("ambiguous"):
        rows.append(
            ("Skipped", Text(f"{result['ambiguous']} names shared by different songs", "muted"))
        )
    fields(term, rows)
    note(
        term, "Read from rekordbox's analysis files by file name; key needs `djlib rekordbox pull`."
    )


STORE_NAMES = {"beatport": "Beatport", "bandcamp": "Bandcamp", "traxsource": "Traxsource"}


@view("requests report")
def request_report(term: Terminal, result: dict) -> None:
    """The shopping list: each missing song with its store searches, one unbroken link a line
    (Cmd-click or right-click opens it), then the songs you own in another mix and the IDs."""
    to_buy = result.get("to_buy")
    if not isinstance(to_buy, list):
        status_line(
            term,
            "ok",
            "Missing-tracks report saved",
            plural(int(result.get("unresolved_items") or 0), "unresolved song"),
        )
        fields(term, [("Report", path_text(result.get("report_path")))])
        return
    others = result.get("other_mixes") or []
    maybe = result.get("maybe_yours") or []
    dash = "—" if term.unicode else "-"
    if to_buy:
        status_line(term, "ok", f"{result.get('name') or 'Set'} {dash} {len(to_buy)} to buy")
    else:
        status_line(term, "ok", result.get("name") or "Set", "nothing to buy")
    width = max((cell_len(song_moment(song)) for song in [*to_buy, *others, *maybe]), default=0)
    if to_buy:
        term.out.print()
    for song in to_buy[:BIG_ROWS]:
        lead = Text(f"{song_moment(song):>{width}} ", style="muted")
        hanging(term.out, lead, Text(str(song.get("label") or ""), style="heading"), indent=2)
        for store, name in STORE_NAMES.items():
            url = (song.get("shop") or {}).get(store)
            if url:
                line = Text.assemble(" " * (width + 4), (f"{name:<10}", "muted"), "  ")
                line.append(url, style="path").stylize(Style(link=url), len(line) - len(url))
                term.out.print(line, soft_wrap=True)
    if len(to_buy) > BIG_ROWS:
        more = len(to_buy) - BIG_ROWS
        # One unbroken line, so the path can be copied whole.
        line = Text(f"  {'…' if term.unicode else '...'} and {more} more in ", style="muted")
        line.append(short_path(result.get("shopping_list")) or "the report", style="path")
        term.out.print(line, soft_wrap=True)
    if maybe:
        term.out.print()
        heading = f"Maybe yours already: check before you buy ({len(maybe)})"
        term.out.print(Text(heading, style="heading"))
        for song in maybe:
            body = Text(str(song.get("label") or ""))
            body.append(f" {dash} you have: {song.get('maybe_owned') or ''}", style="warn")
            lead = Text(f"{song_moment(song):>{width}} ", style="muted")
            hanging(term.out, lead, body, indent=2)
    if others:
        term.out.print()
        term.out.print(Text(f"You own another mix ({len(others)})", style="heading"))
        for song in others:
            body = Text(str(song.get("label") or ""))
            mixes = owned_mixes(song.get("you_own") or [], song.get("title"))
            body.append(f" {dash} you have: {mixes}", style="warn")
            lead = Text(f"{song_moment(song):>{width}} ", style="muted")
            hanging(term.out, lead, body, indent=2)
    ids = result.get("ids") or []
    if ids:
        term.out.print()
        note(term, "IDs, no name yet: " + ", ".join(song_moment(song) for song in ids))
    if result.get("shopping_list"):
        # One unbroken line, so the path can be copied whole.
        term.out.print()
        line = Text.assemble("  ", ("Saved to", "label"), "  ")
        line.append(short_path(result["shopping_list"]), style="path")
        term.out.print(line, soft_wrap=True)
    steps: list[Step] = []
    if maybe:
        steps.append(("If one is the same song, use it", ("ui",)))
    if to_buy:
        term.out.print()
        note(term, "Store searches: check it's the mix you want before you buy.")
        request_id = result.get("request_id", "")
        steps += [
            ("After you buy them, index the new music", ("scan",)),
            ("Then check the set again", ("requests", "refresh", request_id)),
        ]
    if steps:
        next_steps(term, steps)


def song_moment(song: dict) -> str:
    """Where a song plays in the set: its time, else its number."""
    return str(song.get("timestamp") or f"#{song.get('position')}")


# -- delivery ----------------------------------------------------------------------------------

STAGE_LABELS = {
    "prepare_working_copies": "Working copies prepared",
    "bind_target_usb": "USB volume bound",
    "imported": "Imported in app",
    "analyzed": "Analyzed in app",
    "native_exported": "Exported by app",
    "device_library_checked": "Device library checked",
    "hardware_playback": "Played on hardware",
    "device_audio_hash_readback": "USB audio verified",
    "app_working_file_readback": "Working files verified",
}
# Evidence keys whose timestamps date a stage that is named differently in blockers.
STAGE_EVIDENCE = {
    "app_working_file_readback": "app_readback",
    "device_audio_hash_readback": "readback",
}
APP_STAGES = ("prepare_working_copies", "imported", "analyzed", "app_working_file_readback")
USB_STAGES = (
    "prepare_working_copies",
    "bind_target_usb",
    "imported",
    "analyzed",
    "native_exported",
    "device_library_checked",
    "hardware_playback",
    "device_audio_hash_readback",
)
WORKFLOW_LABELS = {
    "rekordbox_import": "rekordbox import",
    "serato_import": "Serato import",
    "rekordbox_usb": "rekordbox USB",
    "serato_portable": "Serato portable USB",
}


@view(
    "delivery get",
    "delivery plan",
    "delivery bind-device",
    "delivery observe",
    "delivery verify-device",
    "delivery verify-app",
)
def delivery_get(term: Terminal, result: dict) -> None:
    if "blockers" not in result:
        generic(term, result)
        return
    request = result.get("request") or {}
    workflow = request.get("workflow", "")
    evidence = result.get("evidence") or {}
    blockers = list(result.get("blockers") or [])
    snapshot = result.get("snapshot") or {}
    track_count = len(snapshot.get("tracks") or snapshot.get("recordings") or [])
    subtitle = " · ".join(
        part
        for part in (
            WORKFLOW_LABELS.get(workflow, workflow),
            request.get("phase") or "",
            plural(track_count, "track") if track_count else "",
            f"app {request.get('app_version')}" if request.get("app_version") else "",
        )
        if part
    )
    header(term, request.get("name") or "Delivery", subtitle)
    term.out.print()
    stages = APP_STAGES if workflow in {"rekordbox_import", "serato_import"} else USB_STAGES
    for stage in stages:
        failed = (evidence.get(stage) or {}).get("outcome") == "failed"
        done = stage not in blockers and not failed
        tone = "bad" if failed else ("ok" if done else "todo")
        style = {"ok": "ok", "bad": "bad", "todo": "muted"}[tone]
        line = Text("  ")
        line.append(term.glyph(tone), style=style)
        line.append(" ")
        line.append(STAGE_LABELS.get(stage, humanize(stage)), style="" if done else "muted")
        observed = (evidence.get(STAGE_EVIDENCE.get(stage, stage)) or {}).get("observed_at") or (
            evidence.get(STAGE_EVIDENCE.get(stage, stage)) or {}
        ).get("checked_at")
        if observed and done:
            line.append(f"  {ago(observed)}", style="muted")
        if failed:
            line.append("  failed", style="bad")
        term.out.print(line)
    extra = [b for b in blockers if b not in stages]
    for blocker in extra:
        term.out.print(
            Text.assemble("  ", (term.glyph("warn"), "warn"), " ", (humanize(blocker), "warn"))
        )
    term.out.print()
    ready = result.get("ready_for_app_use") or result.get("ready_for_departure")
    met = result.get("requirements_met_at_last_check")
    app_only = workflow in {"rekordbox_import", "serato_import"}
    goal = "Ready in the app" if app_only else "Ready to take out"
    if ready:
        status_line(term, "ok", goal, "files verified just now")
    elif met:
        checked = result.get("last_app_readback_at") or result.get("last_audio_readback_at")
        status_line(term, "ok", goal, f"as of the last check, {ago(checked)}")
        note(term, "Based on your recorded observations plus djlib's file checks.")
    native = result.get("next_step") in {
        "imported",
        "analyzed",
        "native_exported",
        "device_library_checked",
        "hardware_playback",
    }
    playlists = ((result.get("preparation_job") or {}).get("result") or {}).get("playlists") or []
    if native and result.get("native_steps"):
        app = "Serato" if "serato" in workflow else "rekordbox"
        term.out.print(Text(f"In {app}", style="heading"))
        steps = Table.grid(padding=(0, 1))
        steps.add_column(style="accent", no_wrap=True)
        steps.add_column(overflow="fold")
        for number, step in enumerate(result["native_steps"], 1):
            steps.add_row(f"{number}.", Text(step.removeprefix(f"In {app}: ")))
        term.out.print(Padding(steps, (0, 0, 0, 2)))
        term.out.print()
    rows: list[tuple[str, object]] = []
    for index, playlist in enumerate(playlists if native else []):
        rows.append(("Playlists" if index == 0 else "", path_text(playlist.get("path"))))
    rows += [
        ("Delivery", Text(result.get("delivery_id", ""), style="muted")),
        ("Revision", result.get("revision")),
    ]
    fields(term, rows)
    next_steps(term, delivery_next_steps(term, result))


def delivery_next_steps(term: Terminal, result: dict) -> list[Step]:
    delivery_id = result.get("delivery_id", "")
    revision = str(result.get("revision", ""))
    step = result.get("next_step") or ""
    if step == "prepare_working_copies":
        return [
            ("Prepare working copies", ("delivery", "prepare", delivery_id, "--revision", revision))
        ]
    if step == "bind_target_usb":
        return [
            (
                "Bind the USB volume",
                ("delivery", "bind-device", delivery_id, "/Volumes/USB", "--revision", revision),
            )
        ]
    if step in {
        "imported",
        "analyzed",
        "native_exported",
        "device_library_checked",
        "hardware_playback",
    }:
        if step == "hardware_playback":
            return [
                (
                    "After playing every pilot track, record it",
                    ("delivery", "observe", delivery_id, "--file", "observation.json"),
                )
            ]
        return [
            (
                f"After checking it in the app, record “{STAGE_LABELS[step].lower()}”",
                ("delivery", "observe", delivery_id),
            )
        ]
    if step in {"app_working_file_readback", "verify_app_before_use"}:
        label = "Re-check before you play" if step == "verify_app_before_use" else "Verify files"
        return [(label, ("delivery", "verify-app", delivery_id, "--revision", revision))]
    if step in {"device_audio_hash_readback", "verify_device_before_departure"}:
        label = "Re-check before you leave" if "departure" in step else "Verify the USB"
        return [(label, ("delivery", "verify-device", delivery_id, "--revision", revision))]
    if step == "eject_safely":
        return [("Eject the USB safely from your OS", None)]
    return []


@view("delivery list")
def delivery_list(term: Terminal, result: dict) -> None:
    rows = result.get("deliveries") or []
    if not rows:
        status_line(term, "todo", "No deliveries yet")
        next_steps(term, [("See supported targets", ("delivery", "targets"))])
        return
    grid = table(
        ("Name", {"style": "heading", "overflow": "ellipsis", "readable": TITLE_WIDTH}),
        ("Workflow", {}),
        ("Stages", {"min_width": 6}),
        ("Delivery", {"style": "muted"}),
    )
    for row in rows:
        stages = Text()
        workflow = row.get("workflow") or ""
        names = (
            ("imported", "analyzed")
            if workflow in {"rekordbox_import", "serato_import"}
            else (
                "imported",
                "analyzed",
                "native_exported",
                "device_library_checked",
                "hardware_playback",
            )
        )
        for name in names:
            outcome = row.get(name)
            glyph, tone = (
                ("ok", "ok")
                if outcome == "passed"
                else ("bad", "bad")
                if outcome == "failed"
                else ("todo", "muted")
            )
            stages.append(term.glyph(glyph), style=tone)
        grid.add_row(
            row.get("name") or "",
            Text(WORKFLOW_LABELS.get(workflow, workflow)).append(
                f" {row.get('phase') or ''}", style="muted"
            ),
            stages,
            short_id(row.get("delivery_id")),
        )
    term.out.print(grid)
    page_hint(term, result, "delivery", len(rows))
    note(term, "Stored observations; run a verify command for a fresh check.")


# -- requests ----------------------------------------------------------------------------------


def mix_name(track: dict) -> str:
    """ "Extended Mix" from either the version field or a title like "Rain (Extended Mix)"."""
    if track.get("version"):
        return track["version"]
    match = TRAILING_VERSION.match(track.get("title") or "")
    return match["version"] if match else track.get("title") or ""


MATCHING = frozenset(
    {"exact_labels", "equivalent_labels", "file_name_labels", "operator_identified"}
)


def _credit_label(credit: dict) -> str:
    text = f"{credit.get('artist') or ''} - {credit.get('title') or ''}"
    return f"{text} ({credit['version']})" if credit.get("version") else text


def spelled_as(item: dict) -> str:
    """The other spelling an owned song's file matched (``accepted.matched_as``): the listing's
    credits ("Robert Glasper") when only they differ, else its whole label; "" when none."""
    label = (item.get("accepted") or {}).get("matched_as") or ""
    source = item.get("input") or {}
    credits = [credit for credit in source.get("also") or [] if isinstance(credit, dict)]
    # The spelling the file matched: the credit with that label, else the first.
    credit = next((c for c in credits if _credit_label(c) == label), credits[0] if credits else {})
    if label and credit.get("artist"):
        mix = credit_form("", credit.get("title") or "", credit.get("version") or "")[1]
        if mix == credit_form("", source.get("title") or "", source.get("version") or "")[1]:
            return credit["artist"]
    return label


REQUEST_STATES = {
    "satisfied": ("ok", "ok", "owned"),
    "missing": ("bad", "bad", "missing"),
    "ambiguous": ("warn", "warn", "pick a copy"),
    "unknown": ("todo", "muted", "ID"),
    "unavailable": ("warn", "warn", "file unavailable"),
    "source_selected": ("run", "info", "source picked"),
    "to_check": ("warn", "warn", "to check"),
    "maybe": ("maybe", "warn", "maybe yours"),
}
ID_LABEL = re.compile(r"^\s*(?:id\s*-\s*)?id\s*$", re.IGNORECASE)  # "ID - ID" is an ID


def maybe_file(item: dict) -> dict | None:
    """A missing song's file under a shorter or longer artist credit, the same title and mix
    (``similar_credits``): maybe yours, once the DJ says so."""
    if item.get("state") != "missing" or (item.get("input") or {}).get("kind") == "unknown":
        return None
    return next(
        (c for c in item.get("candidates") or [] if c.get("identity_match") == "similar_credits"),
        None,
    )


def other_mix(item: dict) -> bool:
    """A missing song you own in another mix: shown as such, never swapped in."""
    return (
        item.get("state") == "missing"
        and (item.get("input") or {}).get("kind") != "unknown"
        and maybe_file(item) is None
        and any(
            c.get("identity_match") == "different_version" for c in item.get("candidates") or []
        )
    )


@view("requests list")
def requests_list(term: Terminal, result: dict) -> None:
    rows = result.get("requests") or []
    if not rows:
        if params().get("query"):
            status_line(term, "todo", "No sets match", f'"{params()["query"]}"')
            return
        status_line(term, "todo", "No sets checked yet")
        note(term, "Check a set's tracklist, and djlib tells you which songs you already own.")
        next_steps(term, [("Copy a set link or tracklist, then", ("set", "paste"))])
        return
    # Services before 0.1.0a16 say only how many songs each set has.
    counted = any(isinstance(row.get("counts"), dict) for row in rows)
    # Owned songs waiting on a pick or a file: counted as `djlib set` counts them.
    checking = counted and any(set_counts(row)["to_check"] for row in rows)
    # Files under other artist credits the DJ can confirm.
    maybe = counted and any(set_counts(row)["maybe"] for row in rows)
    grid = table(
        ("Name", {"style": "heading", "overflow": "ellipsis", "readable": TITLE_WIDTH}),
        *(
            [
                ("Owned", {"justify": "right"}),
                *(
                    [("To check", {"justify": "right", "style": "warn", "drop": 4})]
                    if checking
                    else []
                ),
                *([("Maybe", {"justify": "right", "style": "warn", "drop": 4})] if maybe else []),
                ("Missing", {"justify": "right", "style": "warn"}),
                ("Other mix", {"justify": "right", "style": "warn", "drop": 3}),
                ("IDs", {"justify": "right", "style": "muted", "drop": 2}),
            ]
            if counted
            else [("Songs", {"justify": "right"})]
        ),
        ("Updated", {"style": "muted", "drop": 1}),
        ("ID", {"style": "muted"}),
    )
    for row in rows:
        counts = set_counts(row)
        cells = (
            [
                f"{counts['owned'] - counts['to_check']} of {counts['songs']}",
                *([str(counts["to_check"] or "")] if checking else []),
                *([str(counts["maybe"] or "")] if maybe else []),
                str(counts["missing"] or ""),
                str(counts["other_mix"] or ""),
                str(counts["ids"] or ""),
            ]
            if counted
            else [str(row.get("total_items", ""))]
        )
        grid.add_row(
            row.get("name") or "",
            *cells,
            ago(row.get("updated_at") or row.get("created_at")),
            short_id(row.get("request_id")),
        )
    term.out.print(grid)
    page_hint(term, result, "set", len(rows))


@view("requests collect")
def crate_collected(term: Terminal, job: dict, *, detached: bool = False) -> None:
    """``✓ Crate “Friday”  12 tracks, in set order``, then where to take it."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if job.get("state") != "completed" or not result.get("collection_id"):
        job_card(term, job, detached=detached)
        return
    tracks = int(result.get("selected_count") or 0)
    failed = int((job.get("counts") or {}).get("failed") or 0)
    gaps = job.get("outcome") == "completed_with_gaps" or failed or result.get("excluded_count")
    name = f"Crate “{result.get('name') or ''}”"
    status_line(term, "warn" if gaps else "ok", name, f"{plural(tracks, 'track')}, in set order")
    if gaps:
        # Owned songs that didn't make it in: say why and how to retry before offering the push.
        groups = failure_groups(job)
        rows: list[tuple[str, object]] = [
            ("Failed" if index == 0 else "", Text.assemble((f"{plural(n, 'track')}  ", "bad"), why))
            for index, (_, why, n) in enumerate(groups)
        ]
        if failed and not groups:
            rows.append(("Failed", Text(plural(failed, "track"), style="bad")))
        kind = job.get("kind") or "organize"
        rows += [row for row in job_result_rows(term, kind, result) if row[0] != "Crate"]
        fields(term, rows)
        next_steps(term, job_next_steps(term, job, groups))
        return
    collection = result["collection_id"]
    next_steps(
        term,
        [
            ("Put it in rekordbox", ("rekordbox", "push", collection)),
            ("Or straight onto your USB", ("rekordbox", "usb", collection)),
            ("See its tracks", ("crate", collection)),
        ],
    )


@view("requests get", "requests create", "requests refresh", "requests resolve")
def request_view(term: Terminal, result: dict) -> None:
    if "items" not in result:
        generic(term, result)
        return
    counts = dict(result.get("counts") or {})
    total = result.get("total_items", len(result.get("items") or []))
    header(term, result.get("name") or "Requests", plural(total, "song"))
    # Songs you own in another mix, counted apart from the missing ones when all are here.
    if result.get("next_offset") is None and not result.get("offset"):
        counts["other_mix"] = sum(1 for item in result.get("items") or [] if other_mix(item))
        counts["maybe"] = sum(1 for item in result.get("items") or [] if maybe_file(item))
        counts["missing"] = int(counts.get("missing", 0)) - counts["other_mix"] - counts["maybe"]
    # Owned songs waiting on a pick or a file read "to check", as `djlib set` says it.
    counts["to_check"] = int(counts.pop("ambiguous", 0)) + int(counts.pop("unavailable", 0))
    summary = Text()
    for state in (
        "satisfied",
        "to_check",
        "maybe",
        "other_mix",
        "missing",
        "unknown",
        "source_selected",
    ):
        amount = int(counts.get(state, 0))
        if amount:
            glyph, tone, label = REQUEST_STATES.get(state, ("other", "warn", "in another mix"))
            if summary:
                summary.append(f"  {term.glyph('dot')}  ", style="muted")
            label = "IDs" if label == "ID" and amount != 1 else label
            summary.append(f"{amount} {label}", style="heading" if tone == "ok" else tone)
    term.out.print(indented(summary))
    term.out.print()
    grid = table(
        ("#", {"justify": "right", "style": "muted"}),
        ("Status", {"min_width": 15}),
        ("Requested", {"overflow": "fold", "wrap": True, "readable": TITLE_WIDTH}),
        ("Match", {"overflow": "ellipsis", "drop": 1}),
    )
    for item in result.get("items") or []:
        source = item.get("input") or {}
        glyph, tone, label = REQUEST_STATES.get(
            item.get("state") or "", ("todo", "muted", item.get("state") or "")
        )
        if item.get("state") == "unavailable" and any(
            c.get("availability") == "verification_limit" for c in item.get("candidates") or []
        ):
            glyph, tone, label = "todo", "muted", "not checked"
        if other_mix(item):
            glyph, tone, label = "other", "warn", "other mix"
        maybe = maybe_file(item)
        if maybe:
            glyph, tone, label = "maybe", "warn", "maybe yours"
        if source.get("kind") == "unknown":
            label_text = str(source.get("label") or "ID")
            requested = Text("ID" if ID_LABEL.match(label_text) else label_text, style="muted")
            if source.get("timestamp"):
                requested.append(f"  @ {source['timestamp']}", style="muted")
        else:
            requested = track_label(source)
        accepted = item.get("accepted") or {}
        candidates = item.get("candidates") or []
        others = [c for c in candidates if c.get("identity_match") == "different_version"]
        exact = [c for c in candidates if c.get("identity_match") in MATCHING]
        if accepted.get("path"):
            match = file_tail(accepted["path"])
            if spelling := spelled_as(item):
                match.append(f"  as {spelling}", style="muted")
        elif len(exact) > 1:
            match = Text(f"{len(exact)} copies; pick one", style="warn")
        elif maybe:
            credit = f"{maybe.get('artist') or ''} - {maybe.get('title') or ''}"
            if maybe.get("version"):
                credit += f" ({maybe['version']})"
            match = Text(f"maybe as {credit}", style="warn")
        elif others and item.get("state") == "missing":
            versions = owned_mixes([mix_name(c) for c in others], source.get("title"))
            match = Text(f"you own: {versions}", style="warn")
        elif (item.get("source_selection") or {}).get("source_url"):
            match = Text(item["source_selection"]["source_url"], style="path")
        else:
            match = Text("")
        grid.add_row(
            str(item.get("position", "")),
            marked(term, glyph, label, tone),
            requested,
            match,
        )
    term.out.print(grid)
    request_id = result.get("request_id", "")
    if result.get("next_offset") is not None:
        shown = len(result.get("items") or [])
        more_line(
            term,
            f"{shown} of {plural(int(total or 0), 'song')}",
            ("requests", "get", short_handle(request_id), "--after", str(result["next_offset"])),
        )
    term.out.print()
    note(term, "Matched by exact artist/title/version labels only; no audio identification.")
    steps: list[Step] = []
    owned = int(counts.get("satisfied", 0))
    crate = result.get("crate") if isinstance(result.get("crate"), dict) else {}
    items = result.get("items") or []
    whole = result.get("next_offset") is None and not result.get("offset") and len(items) == total
    # One file per song in a crate, however often the set lists it.
    size = in_crate(items) if whole else owned
    if any(item.get("state") == "ambiguous" for item in result.get("items") or []):
        steps.append(("Pick which copy to use", ("ui",)))
    if any(maybe_file(item) for item in result.get("items") or []):
        steps.append(("Check the songs that may be yours", ("ui",)))
    if owned and crate.get("tracks") == size:
        # Collecting again would only find this same crate.
        steps.append(("See the crate", ("crate", crate["collection_id"])))
    elif owned:
        steps.append(
            (
                f"Build the crate from the {plural(owned, 'song')} you own",
                ("requests", "collect", request_id),
            )
        )
    if result.get("unresolved_items"):
        steps.append(("Your shopping list", ("requests", "report", request_id)))
        steps.append(("Re-check after adding music", ("requests", "refresh", request_id)))
    next_steps(term, steps)


@view("delivery targets")
def delivery_targets(term: Terminal, result: dict) -> None:
    header(term, "Delivery targets")
    term.out.print()
    term.out.print(Text("DJ apps", style="heading"), Text("  no USB or player needed", "muted"))
    apps = table(
        ("Workflow", {"style": "cmd"}),
        ("App", {}),
        ("Formats", {"overflow": "fold"}),
    )
    for key, profile in (result.get("apps") or {}).items():
        formats = ", ".join(sorted((profile.get("audio_formats") or {}).keys()))
        apps.add_row(f"{key}_import", profile.get("name") or key, formats.upper())
    term.out.print(Padding(apps, (0, 0, 0, 2)))
    term.out.print()
    term.out.print(Text("Players", style="heading"), Text("  workflow rekordbox_usb", "muted"))
    players = table(
        ("Profile", {"style": "cmd"}),
        ("Player", {}),
        ("Filesystems", {"overflow": "fold"}),
    )
    for key, profile in (result.get("targets") or {}).items():
        players.add_row(
            key, profile.get("name") or key, ", ".join(profile.get("filesystems") or [])
        )
    term.out.print(Padding(players, (0, 0, 0, 2)))
    term.out.print()
    note(term, "Profiles come from published documentation; they do not prove playback.")


# -- generic fallback --------------------------------------------------------------------------

NOISE = frozenset(
    {
        "schema_version",
        "request_id",
        "warnings",
        "snapshot_scope",
        "evidence_scope",
        "native_state_scope",
    }
)


def generic(term: Terminal, result, *, depth: int = 0) -> None:
    """Readable key/value output for results without a dedicated view."""
    if not isinstance(result, dict):
        term.out.print(Padding(Text(str(result)), (0, 0, 0, 2 * depth)))
        return
    scalars: list[tuple[str, object]] = []
    nested: list[tuple[str, object]] = []
    for key, value in result.items():
        if key in NOISE:
            continue
        if isinstance(value, dict | list) and value:
            nested.append((key, value))
        elif isinstance(value, bool):
            scalars.append(
                (
                    humanize(key),
                    Text(term.glyph("ok"), style="ok") if value else Text("no", style="muted"),
                )
            )
        elif value is None or value == "" or value == [] or value == {}:
            continue
        elif isinstance(value, str) and ("/" in value or "\\" in value) and len(value) > 1:
            scalars.append((humanize(key), path_text(value)))
        elif key.endswith("_at") and isinstance(value, str):
            scalars.append((humanize(key), Text(ago(value) or value, style="muted")))
        else:
            scalars.append((humanize(key), Text(str(value))))  # Text: never read as markup
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="label", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in scalars:
        grid.add_row(Text(label), value if isinstance(value, Text) else Text(str(value)))
    if grid.row_count:
        term.out.print(Padding(grid, (0, 0, 0, 2 + 2 * depth)))
    for key, value in nested:
        term.out.print(Padding(Text(humanize(key), style="heading"), (0, 0, 0, 2 + 2 * depth)))
        if isinstance(value, dict):
            generic(term, value, depth=depth + 1)
        else:
            list_view(term, value, depth + 1)


def list_view(term: Terminal, values: list, depth: int) -> None:
    if all(isinstance(v, dict) for v in values):
        keys: list[str] = []
        for value in values:
            for key, item in value.items():
                if (
                    key not in keys
                    and key not in NOISE
                    and not isinstance(item, dict | list)
                    and len(keys) < 6
                ):
                    keys.append(key)
        grid = table(*[(humanize(k), {"overflow": "fold"}) for k in keys])
        for value in values[:50]:
            grid.add_row(
                *[
                    short_path(str(value.get(k, ""))) if value.get(k) is not None else ""
                    for k in keys
                ]
            )
        term.out.print(Padding(grid, (0, 0, 0, 2 + 2 * depth)))
        if len(values) > 50:
            note(term, f"… {len(values) - 50} more (use --json for everything)")
        return
    for value in values[:50]:
        term.out.print(
            Padding(
                Text.assemble((term.glyph("dot"), "muted"), " ", str(value)),
                (0, 0, 0, 2 + 2 * depth),
            )
        )
