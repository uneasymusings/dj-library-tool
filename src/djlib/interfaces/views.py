"""Readable terminal views for djlib results, keyed by CLI command path."""

from collections.abc import Callable
from pathlib import Path

import typer
from rich import box
from rich.cells import cell_len
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from djlib import __version__
from djlib.domain.contracts import TRAILING_VERSION
from djlib.interfaces.terminal import (
    ACTIVE_STATES,
    JOB_LABELS,
    Terminal,
    ago,
    command_parts,
    duration,
    follow,
    humanize,
    job_progress,
    params,
    path_text,
    plural,
    render_error,
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
    if (
        name in FOLLOW
        and client_factory is not None
        and isinstance(result, dict)
        and result.get("job_id")
        and result.get("state") in ACTIVE_STATES
    ):
        followed = follow(term, client_factory(), result)
        job_card(term, followed.job, detached=followed.detached)
        exit_for_job(followed.job, detached=followed.detached)
        return
    VIEWS.get(name, generic)(term, result)
    for warning in envelope.get("warnings") or []:
        term.err.print(
            Text.assemble((f"{term.glyph('warn')} ", "warn"), str(warning)), soft_wrap=True
        )


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


def header(term: Terminal, title: str, subtitle: str = "", *, glyph: str = "mark") -> None:
    line = Text.assemble((term.glyph(glyph), "brand" if glyph == "mark" else glyph), " ")
    line.append(title, style="heading")
    if subtitle:
        line.append(f"  {subtitle}", style="muted")
    term.out.print(line)


def status_line(term: Terminal, tone: str, message: str, detail: str = "") -> None:
    line = Text.assemble((term.glyph(tone), tone), " ", (message, "heading"))
    if detail:
        line.append(f"  {detail}", style="muted")
    term.out.print(line)


def fields(term: Terminal, rows: list[tuple[str, object]]) -> None:
    """Aligned label/value rows; soft wrapping keeps long paths copy-pasteable."""
    rows = [(label, value) for label, value in rows if value is not None and value != ""]
    if not rows:
        return
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        line = Text("  ")
        line.append(label.ljust(width), style="label")
        line.append("  ")
        line.append_text(value if isinstance(value, Text) else Text(str(value)))
        term.out.print(line, soft_wrap=True)


def next_steps(term: Terminal, steps: list[tuple[str, tuple | None]]) -> None:
    """Copy-pasteable follow-ups; long commands move under their label, unbroken."""
    steps = [step for step in steps if step]
    if not steps:
        return
    term.out.print()
    term.out.print(Text("Next", style="heading"))
    width = max(len(label) for label, _ in steps)
    for label, parts in steps:
        line = Text.assemble("  ", (term.glyph("arrow"), "accent"), " ", label)
        if parts:
            command = term.command(*parts)
            if 6 + width + len(command) <= term.out.width:
                line.append(" " * (width - len(label) + 2))
            else:
                line.append("\n    ")
            line.append(command, style="cmd")
        term.out.print(line, soft_wrap=True)


def note(term: Terminal, message: str) -> None:
    term.out.print(Padding(Text(message, style="muted"), (0, 0, 0, 2)))


MIN_FLEX = 6
READABLE_FLEX = 12  # optional columns drop before flexible text gets narrower than this
GUTTER = 3  # cell padding on both sides plus SIMPLE_HEAD's blank divider


class Grid:
    """One line per row, fitted to the terminal at render time.

    Fixed columns (IDs, times, badges) keep their natural width; flexible text columns
    (``overflow`` set) shrink longest-first; ``drop`` columns disappear on narrow
    terminals, lowest number first. Rich alone would hide whole columns instead.
    """

    def __init__(self, *columns: str | tuple[str, dict]):
        self.columns = [column if isinstance(column, tuple) else (column, {}) for column in columns]
        self.rows: list[list] = []

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def add_row(self, *cells) -> None:
        self.rows.append(list(cells))

    def __rich_console__(self, console, options):
        count = len(self.columns)
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
        active = list(range(count))

        def needed(columns):
            return sum(
                min(natural[i], READABLE_FLEX) if flexible[i] else natural[i] for i in columns
            ) + GUTTER * (len(columns) - 1)

        droppable = sorted(
            (spec["drop"], index) for index, (_, spec) in enumerate(self.columns) if "drop" in spec
        )
        while needed(active) > options.max_width and droppable:
            active.remove(droppable.pop(0)[1])
        widths = {i: natural[i] for i in active}
        room = options.max_width - GUTTER * (len(active) - 1)
        room -= sum(widths[i] for i in active if not flexible[i])
        shrinkable = [i for i in active if flexible[i]]
        while shrinkable and sum(widths[i] for i in shrinkable) > room:
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
        )
        passthrough = {"justify", "style", "header_style"}
        for index in active:
            name, spec = self.columns[index]
            grid.add_column(
                name,
                width=widths[index],
                no_wrap=True,
                overflow="ellipsis",
                **{key: value for key, value in spec.items() if key in passthrough},
            )
        for row in self.rows:
            grid.add_row(*(row[index] for index in active))
        yield grid


def table(*columns: str | tuple[str, dict]) -> Grid:
    return Grid(*columns)


def page_hint(term: Terminal, result: dict, noun: str, shown: int) -> None:
    total = result.get("total")
    cursor = result.get("next_cursor")
    summary = f"{shown} of {plural(total, noun)}" if isinstance(total, int) else plural(shown, noun)
    line = Text(summary, style="muted")
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
        line.append(f"  {term.glyph('dot')}  more: ", style="muted")
        line.append(term.command(*parts), style="cmd")
    term.out.print()
    term.out.print(line)


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
            return Text(f"{term.glyph('warn')} partial", style="warn")
        return Text(f"{term.glyph('ok')} done", style="ok")
    glyph, tone, label = {
        "queued": ("todo", "muted", "queued"),
        "running": ("run", "info", "running"),
        "paused": ("pause", "warn", "paused"),
        "needs_attention": ("warn", "warn", "needs review"),
        "failed": ("bad", "bad", "failed"),
        "cancelled": ("skip", "muted", "cancelled"),
    }.get(state or "", ("todo", "muted", state or "unknown"))
    return Text(f"{term.glyph(glyph)} {label}", style=tone)


COUNT_TONES = {
    "succeeded": "ok",
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


def job_card(term: Terminal, job: dict, *, detached: bool = False, timed_out: bool = False):
    kind = job.get("kind") or "job"
    label = JOB_LABELS.get(kind, humanize(kind))
    state, outcome = job.get("state"), job.get("outcome")
    title = label.split(" ", 1)[0] if state == "completed" else label
    if state == "completed":
        tone = "warn" if outcome == "completed_with_gaps" else "ok"
        message = {
            "scan": "Music indexed",
            "collection": "Collection built",
            "download": "Downloads finished",
            "export": "Export ready",
            "delivery": "Working copies prepared",
            "delivery_check": "Delivery check recorded",
            "organize": "Collection organized",
            "organization": "Collection organized",
            "reconcile": "Changes reconciled",
        }.get(kind, f"{title} finished")
        if tone == "warn":
            message += ", with gaps"
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
    status_line(term, tone if tone in {"ok", "warn", "bad"} else tone, message)
    done, total = job_progress(job.get("counts") or {})
    rows: list[tuple[str, object]] = []
    if total:
        progress = counts_text(term, job.get("counts"))
        if state in ACTIVE_STATES:
            progress = Text(f"{done}/{total}  ", style="").append_text(progress)
        rows.append(("Items", progress))
    rows += job_result_rows(term, kind, job.get("result") or {})
    rows.append(("Job", Text(job.get("job_id", ""), style="muted")))
    if job.get("updated_at"):
        rows.append(("Updated", Text(ago(job["updated_at"]), style="muted")))
    fields(term, rows)
    if detached:
        note(term, "The job keeps running in the background; closing this terminal is safe.")
    elif timed_out:
        note(term, "Still running in the background.")
    next_steps(term, job_next_steps(term, job))


def job_result_rows(term: Terminal, kind: str, result: dict) -> list[tuple[str, object]]:
    if not isinstance(result, dict):
        return []
    rows: list[tuple[str, object]] = []
    if kind == "export":
        rows += [
            ("Tracks", result.get("track_count")),
            ("Playlist", path_text(result.get("playlist_path"))),
            ("rekordbox XML", path_text(result.get("rekordbox_xml_path"))),
            ("Manifest", path_text(result.get("manifest_path"))),
        ]
        return rows
    if kind in {"organize", "organization"} and "selected_count" in result:
        rows.append(("Selected", plural(int(result.get("selected_count") or 0), "track")))
        if result.get("excluded_count"):
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
    if result.get("collection_id"):
        rows.append(("Collection", Text(result["collection_id"], style="")))
    if result.get("delivery_id"):
        rows.append(("Delivery", Text(result["delivery_id"], style="")))
    for index, playlist in enumerate(result.get("playlists") or []):
        rows.append(("Playlists" if index == 0 else "", path_text(playlist.get("path"))))
    if result.get("error"):
        error = result["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        rows.append(("Error", Text(str(message), style="bad")))
    return rows


def job_next_steps(term: Terminal, job: dict) -> list[tuple[str, tuple | None]]:
    job_id = job.get("job_id", "")
    state, kind = job.get("state"), job.get("kind")
    counts = job.get("counts") or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    steps: list[tuple[str, tuple | None]] = []
    if state in ACTIVE_STATES:
        steps.append(("Watch progress", ("jobs", "watch", job_id)))
        return steps
    if state == "needs_attention" or counts.get("needs_input"):
        steps.append(("Review conflicts", ("reviews", "list", "--job-id", job_id)))
    if state == "paused":
        steps.append(("Resume", ("jobs", "control", job_id, "resume")))
    if counts.get("failed") or state == "failed":
        steps.append(("See what failed", ("jobs", "items", job_id, "--state", "failed")))
        if state != "cancelled":
            steps.append(("Retry failed items", ("jobs", "control", job_id, "retry")))
    if state == "completed":
        if kind == "scan":
            steps.append(("Browse your library", ("library",)))
        elif result.get("collection_id"):
            steps.append(("Open the collection", ("collection", result["collection_id"])))
            if kind in {"collection", "organize"}:
                steps.append(
                    (
                        "Prepare it for rekordbox",
                        (
                            "delivery",
                            "plan",
                            "--collection",
                            result["collection_id"],
                            "--workflow",
                            "rekordbox_import",
                            "--app-version",
                            "VERSION",
                        ),
                    )
                )
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
        next_steps(term, [("Index a music folder", ("scan", "PATH"))])
        return
    grid = table(
        ("State", {}),
        ("Kind", {}),
        ("Name", {"overflow": "ellipsis", "drop": 2}),
        ("Items", {"overflow": "ellipsis", "drop": 3}),
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
            job.get("job_id", ""),
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
        job_id = params().get("job_id", "")
        term.out.print()
        term.out.print(
            Text.assemble(
                ("more: ", "muted"),
                (
                    term.command("jobs", "items", job_id, "--after", str(result["next_cursor"])),
                    "cmd",
                ),
            )
        )


def item_badge(term: Terminal, state: str | None) -> Text:
    glyph, tone = {
        "succeeded": ("ok", "ok"),
        "failed": ("bad", "bad"),
        "skipped": ("skip", "warn"),
        "needs_input": ("warn", "warn"),
        "running": ("run", "info"),
        "pending": ("todo", "muted"),
        "cancelled": ("skip", "muted"),
    }.get(state or "", ("todo", "muted"))
    label = "needs review" if state == "needs_input" else (state or "")
    return Text(f"{term.glyph(glyph)} {label}", style=tone)


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
    show_file = not numbered
    columns: list[tuple[str, dict]] = []
    if numbered:
        columns.append(("#", {"justify": "right", "style": "muted"}))
    columns += [
        ("Artist", {"overflow": "ellipsis"}),
        ("Title", {"overflow": "ellipsis", "style": "heading"}),
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
            next_steps(term, [("Index a music folder", ("scan", "PATH"))])
        return
    term.out.print(track_table(term, tracks))
    page_hint(term, result, "track", len(tracks))


@view("collections")
def collections(term: Terminal, result: dict) -> None:
    rows = result.get("collections") or []
    if not rows:
        status_line(term, "todo", "No collections yet")
        note(term, "Ask your assistant to build one, or plan one from a JSON tracklist.")
        next_steps(term, [("Plan a collection", ("plan", "--file", "collection.json"))])
        return
    grid = table(
        ("Name", {"style": "heading", "overflow": "ellipsis"}),
        ("Tracks", {"justify": "right"}),
        ("Created", {"style": "muted", "drop": 1}),
        ("Collection", {"style": "muted"}),
    )
    for row in rows:
        grid.add_row(
            row.get("name") or "",
            str(row.get("track_count", "")),
            ago(row.get("created_at")),
            row.get("collection_id", ""),
        )
    term.out.print(grid)
    page_hint(term, result, "collection", len(rows))


@view("collection")
def collection(term: Terminal, result: dict) -> None:
    tracks = result.get("tracks") or []
    total = result.get("track_count", len(tracks))
    header(term, result.get("name") or "Collection", plural(total, "track"))
    if tracks:
        term.out.print()
        offset = int(params().get("after") or 0)
        term.out.print(track_table(term, tracks, numbered=True, start=offset))
    term.out.print()
    fields(term, [("Collection", Text(result.get("collection_id", ""), style="muted"))])
    if result.get("next_cursor") is not None:
        next_steps(
            term,
            [
                (
                    "Next page",
                    (
                        "collection",
                        result.get("collection_id", ""),
                        "--after",
                        str(result["next_cursor"]),
                    ),
                )
            ],
        )
    next_steps(
        term,
        [("Export M3U + rekordbox XML", ("export", result.get("collection_id", "")))]
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
        next_steps(term, [("Allow a folder", ("roots", "add", "PATH"))])
        return
    header(term, "Music folders", "djlib only reads inside these")
    for root in allowed:
        exists = Path(root).is_dir()
        line = Text("  ")
        line.append(term.glyph("ok" if exists else "bad"), style="ok" if exists else "bad")
        line.append(" ")
        line.append(short_path(root), style="path")
        if root in added:
            line.append("  new", style="accent")
        if not exists:
            line.append("  not found (unplugged drive?)", style="bad")
        term.out.print(line)
    if added and len(added) == 1:
        next_steps(term, [("Index it", ("scan", next(iter(added))))])


# -- setup and service -------------------------------------------------------------------------


@view("version")
def version_view(term: Terminal, result: dict) -> None:
    header(term, "djlib", result.get("version", __version__))


@view("init")
def init_view(term: Terminal, result: dict) -> None:
    config = result.get("config") or {}
    allowed = config.get("allowed_roots") or []
    header(
        term, "Workspace ready", "set as your default" if result.get("default_workspace") else ""
    )
    fields(
        term,
        [
            ("Workspace", path_text(result.get("workspace"))),
            *[("Music" if i == 0 else "", path_text(root)) for i, root in enumerate(allowed)],
        ],
    )
    steps = (
        [("Index your music", ("scan",) if len(allowed) == 1 else ("scan", "PATH"))]
        if allowed
        else [("Allow a music folder", ("roots", "add", "PATH"))]
    )
    steps.append(("Check your setup", ("doctor",)))
    next_steps(term, steps)


@view("ui")
def review_page(term: Terminal, result: dict) -> None:
    header(term, "Review page", "opening in your browser")
    term.out.print(Text("  " + (result.get("url") or ""), style="path"), soft_wrap=True)
    term.out.print()
    note(term, "The link contains this workspace's access token. Keep it to yourself.")
    note(term, "It works while djlib's background service is running.")


@view("status")
def status_view(term: Terminal, result: dict) -> None:
    tracks = int(result.get("tracks") or 0)
    header(term, "djlib", short_path(result.get("workspace")))
    service = result.get("service_url")
    bpm, key = int(result.get("with_bpm") or 0), int(result.get("with_key") or 0)
    fields(
        term,
        [
            ("Library", f"{plural(tracks, 'track')}  ·  BPM for {bpm}  ·  key for {key}"),
            (
                "Service",
                Text(f"{term.glyph('ok')} running", style="ok")
                if service
                else Text("stopped; starts when needed", style="muted"),
            ),
            (
                "Analysis",
                Text(f"rekordbox synced {ago(result['rekordbox_analysis_synced_at'])}", "muted")
                if result.get("rekordbox_analysis_synced_at")
                else None,
            ),
        ],
    )
    requests = result.get("recent_requests") or []
    if requests:
        term.out.print()
        term.out.print(Text("Request lists", style="heading"))
        for row in requests:
            line = Text("  ")
            done = not row.get("unresolved")
            line.append(term.glyph("ok" if done else "todo"), style="ok" if done else "warn")
            line.append(f" {row.get('name', '')}", style="heading")
            line.append(f"  {row.get('owned', 0)}/{row.get('songs', 0)} owned", style="muted")
            if row.get("missing"):
                line.append(f"  {row['missing']} missing", style="warn")
            term.out.print(line, soft_wrap=True)
    collections = result.get("recent_collections") or []
    if collections:
        term.out.print()
        term.out.print(Text("Crates", style="heading"))
        for row in collections:
            line = Text("  ")
            line.append(f"{row.get('name', '')}", style="heading")
            line.append(f"  {plural(int(row.get('tracks') or 0), 'track')}", style="muted")
            if row.get("in_rekordbox") is True:
                line.append(f"  {term.glyph('ok')} in rekordbox", style="ok")
            elif row.get("in_rekordbox") is False:
                line.append("  not in rekordbox yet", style="muted")
            term.out.print(line, soft_wrap=True)
    steps: list[tuple[str, tuple | None]] = []
    if not tracks:
        steps.append(("Index your music", ("scan",)))
    for row in collections:
        if row.get("in_rekordbox") is False:
            steps.append(
                (
                    f"Put “{row['name']}” in rekordbox",
                    ("rekordbox", "push", row["collection_id"], "--when-idle", "60"),
                )
            )
            break
    for row in requests:
        if row.get("needs_check"):
            steps.append(
                (
                    f"Re-check “{row['name']}”",
                    ("requests", "refresh", row["request_id"], "--revision", str(row["revision"])),
                )
            )
            break
    for row in requests:
        if (
            row.get("owned")
            and not row.get("needs_check")
            and not any(c["name"] == row["name"] for c in collections)
        ):
            steps.append(
                (f"Make a crate from “{row['name']}”", ("requests", "collect", row["request_id"]))
            )
            break
    if bpm > key and result.get("rekordbox_checked"):
        steps.append(("Bring in musical key", ("rekordbox", "pull", "--when-idle", "120")))
    next_steps(term, steps)


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
        next_steps(term, [("Create it", ("init", "--allow-root", "PATH"))])
    elif result.get("changed"):
        note(term, "Commands now use it without --workspace.")


@view("doctor")
def doctor(term: Terminal, result: dict) -> None:
    header(term, "djlib doctor", __version__)
    runtimes = result.get("javascript_runtimes") or {}
    selected = runtimes.get(runtimes.get("selected") or "") or {}
    checks = [
        ("Workspace", True, short_path(result.get("workspace")) or "", ""),
        (
            "Background service",
            bool(result.get("coordinator_url")),
            result.get("coordinator_url") or "not running",
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
            "YouTube JS runtime",
            bool(runtimes.get("youtube_runtime_ready")),
            f"{runtimes.get('selected')} {selected.get('version') or ''}".strip()
            if runtimes.get("selected")
            else "none",
            "" if runtimes.get("youtube_runtime_ready") else "optional; Deno 2.3+ or Node 22+",
        ),
    ]
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, min_width=max(len(check[0]) for check in checks) + 2)
    grid.add_column(overflow="fold")
    for name, passed, value, hint in checks:
        optional = "optional" in hint or name == "Background service"
        tone = "ok" if passed else ("todo" if optional else "bad")
        style = {"ok": "ok", "todo": "muted", "bad": "bad"}[tone]
        detail = Text(value, style="path" if passed and "/" in value else "")
        if hint:
            detail.append(f"  {hint}", style="muted")
        grid.add_row(Text(f"{term.glyph(tone)} ", style=style) + Text(name), detail)
    term.out.print(Padding(grid, (0, 0, 0, 2)))
    term.out.print()
    note(
        term,
        "Native import, analysis and USB export happen in rekordbox or Serato; "
        "djlib prepares files and records what you observe there.",
    )


@view("capabilities")
def capabilities(term: Terminal, result: dict) -> None:
    header(term, "Capabilities", result.get("application_version", ""))
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()
    implemented = [
        Text(f"{term.glyph('ok')} ", "ok") + humanize(x) for x in result.get("implemented") or []
    ]
    planned = [
        Text(f"{term.glyph('todo')} ", "muted") + humanize(x) for x in result.get("planned") or []
    ]
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


@view("setup-agent")
def setup_agent(term: Terminal, result: dict) -> None:
    header(term, "Assistant session ready")
    output = result.get("output") or result.get("session") or result.get("path")
    fields(
        term, [("Session", path_text(output)), ("Workspace", path_text(result.get("workspace")))]
    )
    launches = [
        (f"Start {host.title()}", result.get(f"launch_{host}")) for host in ("claude", "codex")
    ]
    term.out.print()
    term.out.print(Text("Next", style="heading"))
    for label, command in launches:
        if command:
            line = Text.assemble((term.glyph("arrow"), "accent"), f" {label}  ")
            line.append(" ".join(shell_word(part) for part in command), style="cmd")
            term.out.print(Padding(line, (0, 0, 0, 2)))


def shell_word(value: str) -> str:
    from djlib.interfaces.terminal import quote

    return quote(short_path(value)) if "/" in value else quote(value)


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
            ("Collection", Text(collection_id)),
            ("Playlist", path_text(export.get("playlist_path"))),
            ("rekordbox XML", path_text(export.get("rekordbox_xml_path"))),
        ],
    )
    next_steps(
        term,
        [
            ("Browse the library", ("library",)),
            ("Open the collection", ("collection", collection_id)),
            ("Stop the background service", ("service", "stop")),
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
        meter = Text(term.glyph("bar_full") * filled, style="accent")
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
                    (f"{term.glyph('ok')} fits", "ok")
                    if enough
                    else (f"{term.glyph('bad')} not enough space", "bad"),
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
        ("Matched", Text(plural(matched, "track"), style="ok" if matched else "")),
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
    status_line(
        term,
        "ok",
        f"In rekordbox: {plural(len(crates), 'playlist')}",
        f"rekordbox in front for {result.get('rekordbox_ui_seconds', 0):g} s",
    )
    for crate in crates:
        line = Text("  ")
        imported = crate.get("status") == "imported"
        line.append(term.glyph("ok"), style="ok")
        line.append(f" {crate.get('playlist', '')}", style="heading")
        line.append("  imported" if imported else "  already there", style="muted")
        if "expected" in crate:
            complete = crate["matched"] == crate["expected"]
            line.append(
                f"  {crate['matched']}/{crate['expected']} tracks",
                style="ok" if complete else "warn",
            )
            line.append(f", {crate['analyzed']} analyzed", style="muted")
        term.out.print(line)
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


@view("rekordbox usb")
def rekordbox_usb(term: Terminal, result: dict) -> None:
    found, expected = int(result.get("found") or 0), int(result.get("expected") or 0)
    complete = expected and found == expected
    status_line(
        term,
        "ok" if complete else "warn",
        f"“{result.get('playlist', '')}” on {Path(result.get('device') or '').name}",
        f"exported in {result.get('export_seconds', 0):g} s",
    )
    fields(
        term,
        [
            (
                "Tracks",
                Text(
                    f"{found} of {expected} on the USB, byte for byte", "ok" if complete else "warn"
                ),
            ),
            (
                "Library",
                Text(f"{term.glyph('ok')} rekordbox device library updated", "ok")
                if result.get("library_updated")
                else Text("device library unchanged", "warn"),
            ),
        ],
    )
    for label in (result.get("missing") or [])[:5]:
        note(term, f"missing: {label}")
    term.out.print()
    note(
        term,
        "Exported by rekordbox; djlib only read the stick. Test it on your player before a gig.",
    )


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


@view("requests report")
def request_report(term: Terminal, result: dict) -> None:
    status_line(
        term,
        "ok",
        "Missing-tracks report saved",
        plural(int(result.get("unresolved_items") or 0), "unresolved song"),
    )
    fields(term, [("Report", path_text(result.get("report_path")))])


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
            steps.add_row(f"{number}.", step.removeprefix(f"In {app}: "))
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


def delivery_next_steps(term: Terminal, result: dict) -> list[tuple[str, tuple | None]]:
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
        ("Name", {"style": "heading", "overflow": "ellipsis"}),
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
            row.get("delivery_id", ""),
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
REQUEST_STATES = {
    "satisfied": ("ok", "ok", "owned"),
    "missing": ("bad", "bad", "missing"),
    "ambiguous": ("warn", "warn", "ambiguous"),
    "unknown": ("todo", "muted", "unknown ID"),
    "unavailable": ("warn", "warn", "unavailable"),
    "source_selected": ("run", "info", "source picked"),
}


@view("requests list")
def requests_list(term: Terminal, result: dict) -> None:
    rows = result.get("requests") or []
    if not rows:
        status_line(term, "todo", "No request lists yet")
        note(term, "Save the songs you want, and djlib checks which ones you already own.")
        next_steps(term, [("Create one", ("requests", "create", "--file", "wanted.json"))])
        return
    grid = table(
        ("Name", {"style": "heading", "overflow": "ellipsis"}),
        ("Songs", {"justify": "right"}),
        ("Updated", {"style": "muted", "drop": 1}),
        ("Request", {"style": "muted"}),
    )
    for row in rows:
        grid.add_row(
            row.get("name") or "",
            str(row.get("total_items", "")),
            ago(row.get("updated_at") or row.get("created_at")),
            row.get("request_id", ""),
        )
    term.out.print(grid)
    page_hint(term, result, "request list", len(rows))


@view("requests get", "requests create", "requests refresh", "requests resolve")
def request_view(term: Terminal, result: dict) -> None:
    if "items" not in result:
        generic(term, result)
        return
    counts = result.get("counts") or {}
    total = result.get("total_items", len(result.get("items") or []))
    header(term, result.get("name") or "Requests", plural(total, "song"))
    summary = Text("  ")
    for state in ("satisfied", "missing", "ambiguous", "unknown", "unavailable", "source_selected"):
        amount = int(counts.get(state, 0))
        if amount:
            glyph, tone, label = REQUEST_STATES[state]
            if len(summary) > 2:
                summary.append(f"  {term.glyph('dot')}  ", style="muted")
            summary.append(f"{amount} {label}", style=tone)
    term.out.print(summary)
    term.out.print()
    grid = table(
        ("#", {"justify": "right", "style": "muted"}),
        ("Status", {"min_width": 15}),
        ("Requested", {"overflow": "ellipsis"}),
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
        if source.get("kind") == "unknown":
            requested = Text(source.get("label") or "Unknown", style="muted")
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
        elif len(exact) > 1:
            match = Text(f"{len(exact)} copies; pick one", style="warn")
        elif others and item.get("state") == "missing":
            versions = ", ".join(dict.fromkeys(mix_name(c) for c in others))
            match = Text(f"you own: {versions}", style="warn")
        elif (item.get("source_selection") or {}).get("source_url"):
            match = Text(item["source_selection"]["source_url"], style="path")
        else:
            match = Text("")
        grid.add_row(
            str(item.get("position", "")),
            Text(f"{term.glyph(glyph)} {label}", style=tone),
            requested,
            match,
        )
    term.out.print(grid)
    if result.get("next_offset") is not None:
        next_steps(
            term,
            [
                (
                    "Next page",
                    (
                        "requests",
                        "get",
                        result.get("request_id", ""),
                        "--after",
                        str(result["next_offset"]),
                    ),
                )
            ],
        )
    term.out.print()
    fields(
        term,
        [
            ("Request", Text(result.get("request_id", ""), style="muted")),
            ("Revision", result.get("revision")),
        ],
    )
    note(term, "Matched by exact artist/title/version labels only; no audio identification.")
    steps: list[tuple[str, tuple | None]] = []
    if result.get("unresolved_items"):
        steps.append(
            (
                "Save a missing-tracks report",
                (
                    "requests",
                    "report",
                    result.get("request_id", ""),
                    "--revision",
                    str(result.get("revision", "")),
                ),
            )
        )
        steps.append(
            (
                "Re-check after adding music",
                (
                    "requests",
                    "refresh",
                    result.get("request_id", ""),
                    "--revision",
                    str(result.get("revision", "")),
                ),
            )
        )
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
            scalars.append((humanize(key), escape(str(value))))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="label", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in scalars:
        grid.add_row(label, value if isinstance(value, Text) else Text(str(value)))
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
