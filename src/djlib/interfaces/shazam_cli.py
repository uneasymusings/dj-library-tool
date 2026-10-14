"""`djlib shazam …` and `djlib set shazam[:SPEC]`: the songs you Shazamed, as a set.

A Shazam history becomes a request list in the order you heard the songs, so it gets
owned/missing, downloads, a crate, rekordbox and USB like any other set.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import typer

from djlib.domain.errors import AppError
from djlib.interfaces.envelope import envelope
from djlib.interfaces.terminal import plural, short_id
from djlib.sources import shazam_history

MAX_SONGS = 1000  # a request list's limit


def is_spec(value: str) -> bool:
    """``shazam`` or ``shazam:…`` (a file of that name still counts as a tracklist)."""
    head = value.split(":", 1)[0].strip().lower()
    return head == "shazam" and not Path(value).expanduser().is_file()


def labels(item: dict) -> tuple[str, str, str]:
    """Artist, title and mix the way tracklist lines are read: "Rain (Extended Mix)" and
    "Rain - Extended Mix" have the version "Extended Mix"; "(feat. Ana)" stays in the title."""
    from djlib.application.tracklists import FEATURING_LABEL, SEPARATOR, VERSION_WORDS
    from djlib.domain.contracts import TRAILING_VERSION

    artist = " ".join((item.get("artist") or "").split())
    title = " ".join((item.get("title") or "").split())
    match = TRAILING_VERSION.match(title)
    if (
        match
        and VERSION_WORDS.search(match["version"])
        and not FEATURING_LABEL.match(match["version"])
    ):
        return artist, match["title"].strip(), match["version"].strip()
    parts = SEPARATOR.split(title)
    if len(parts) > 1 and VERSION_WORDS.search(parts[-1]):
        return artist, " - ".join(parts[:-1]), parts[-1].strip()
    return artist, title, ""


def note(item: dict) -> str:
    """ "Shazamed 2026-10-02 21:14 · Berlin" in this Mac's time zone (no bare coordinates)."""
    parts = []
    if item.get("shazamed_at"):
        parts.append(f"Shazamed {shazam_history.local_time(item['shazamed_at'])}")
    if place := shazam_history.place_name(item.get("location")):
        parts.append(str(place))
    return " · ".join(parts)[:4000]


def describe(loaded: dict, songs: int, shazams: int | None = None) -> str:
    """ "Music Recognition on this Mac, 42 songs since 2026-10-01"; "CSV file, 1,000 Shazams ·
    983 different songs" when some songs were Shazamed more than once."""
    where = " + ".join(source["label"] for source in loaded["sources"]) or "Shazam"
    text = f"{where}, {songs:,} song{'s' if songs != 1 else ''}"
    if shazams is not None and shazams > songs:
        text = f"{where}, {shazams:,} Shazams · {songs:,} different song{'s' if songs != 1 else ''}"
    if loaded["since"]:
        text += f" since {shazam_history.local_time(loaded['since'])}"
    return text


def nothing_new(loaded: dict) -> AppError:
    """SHAZAM_NOTHING_NEW: a normal state, not a failure (a terminal shows it calmly), with
    the last Shazam crate as the next step for ``shazam:new``."""
    since = loaded["since"]
    when = f" since {shazam_history.day_time(since)}" if since else ""
    details = {"since": since, "songs_in_history": loaded["total"]}
    again = "Shazam some songs, wait a few minutes for them to sync, then run this again."
    if not loaded["new"]:
        details.update(
            headline=f"Nothing Shazamed{when}",
            next="djlib shazam list",
            next_label="See what you Shazamed",
        )
    else:
        # ``since`` is the newest Shazam imported last time.
        last = loaded["last_import"] or {}
        details["last_import"] = {
            key: last.get(key) for key in ("request_id", "collection_id", "name", "imported_at")
        }
        details["headline"] = f"Nothing new{when}"
        # The list holding that Shazam, which an older list imported since doesn't.
        newest = last.get("newest_list") or last
        if newest.get("collection_id"):
            details.update(
                next=f"djlib crate {short_id(newest['collection_id'])}",
                next_label="Your last Shazam crate",
            )
        elif newest.get("request_id"):  # none of its songs were owned, so it has no crate
            details.update(
                next=f"djlib requests get {short_id(newest['request_id'])}",
                next_label="Your last Shazam list",
            )
    message = f"{details['headline']}. {again}"
    return AppError("SHAZAM_NOTHING_NEW", message, details=details)


def set_request(spec: str, name: str | None, workspace) -> tuple:
    """(request body, warnings, summary) for ``djlib set shazam[:SPEC]``.

    A list holds MAX_SONGS songs. From this Mac's history it takes the oldest and
    ``summary["remaining"]`` newer ones come next with ``shazam:new``; from a file or Apple
    Music it takes the newest and leaves out the ``remaining`` oldest.

    The summary goes into the set's result and, once every step of the set is done, into
    ``shazam_history.remember_import`` so ``shazam:new`` starts after its newest song.
    """
    from djlib.domain.request_contracts import RequestCreate, RequestItem

    source, since = shazam_history.parse_spec(spec)
    loaded = shazam_history.load(source, since, workspace)
    records = loaded["records"]
    if not records:
        raise nothing_new(loaded)
    items, used, left_out = [], [], 0
    for item in records:
        artist, title, version = labels(item)
        try:
            request = RequestItem(
                artist=artist[:500] or None,
                title=title[:1000] or None,
                version=version[:300],
                notes=note(item),
            )
        except ValueError:
            left_out += 1
            continue
        items.append(request)
        used.append(item)
    if not items:
        raise AppError("INPUT_INVALID", "None of these Shazams has both an artist and a title.")
    remaining = max(0, len(items) - MAX_SONGS)
    # shazam:new continues this Mac's history after a full list; nothing continues a file or
    # Apple Music, so those keep their newest songs.
    continues = source is None
    left = [] if continues else used[:remaining]
    if continues:
        items, used = items[:MAX_SONGS], used[:MAX_SONGS]
    else:
        items, used = items[remaining:], used[remaining:]
    first_times = [item["shazamed_at"] for item in used if item["shazamed_at"]]
    first = min(first_times, default=None)
    # With songs left over, shazam:new continues after the last first Shazam in this list,
    # skipping repeats of its songs up to newest_in_history (shazam_history.unseen).
    newest = (
        max(first_times, default=None) if remaining and continues else shazam_history.newest(used)
    )
    next_spec = continuation(workspace, newest) if remaining and continues else None
    # Named after the days it covers, so a crate or playlist says which nights it holds.
    title = (name or shazam_history.set_name(loaded["sources"], first, newest))[:300]
    parsed = json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True)
    digest = hashlib.sha256(f"{title}\n{parsed}".encode()).hexdigest()[:16]
    body = RequestCreate(name=title, items=items, idempotency_key=f"shazam:{digest}")
    warnings = [
        f"{problem['message']} ({shazam_history.LABELS.get(problem['source'], problem['source'])})"
        for problem in loaded["problems"]
    ]
    if left_out:
        warnings.append(f"Left out {plural(left_out, 'Shazam')} without an artist or title.")
    if remaining and continues:
        # Dates as in the list's name; the command is under Next, in one piece.
        days = f" ({shazam_history.day_range(first, newest)})" if first and newest else ""
        rest = "the other one comes" if remaining == 1 else f"the other {remaining:,} come"
        warnings.append(f"This list has your oldest {MAX_SONGS:,} Shazams{days}; {rest} next.")
    elif remaining:
        if all(item["shazamed_at"] for item in left):
            which = f"your {remaining:,} oldest Shazam{'s' if remaining != 1 else ''}"
        else:
            where = (
                shazam_history.LABELS["apple_music"]
                if source == shazam_history.APPLE_MUSIC
                else Path(source).name
            )
            which = f"the first {remaining:,} song{'s' if remaining != 1 else ''} in {where}"
        warnings.append(f"A list holds {MAX_SONGS:,} songs, so this one leaves out {which}.")
    summary = {
        "name": title,
        "description": describe(loaded, len(items), sum(int(i.get("count") or 1) for i in used)),
        "sources": loaded["sources"],
        "since": loaded["since"],
        "new": loaded["new"],
        "songs": len(items),
        "songs_in_history": loaded["total"],
        "first_shazamed_at": first,
        "newest_shazamed_at": newest,
        "newest_in_history": loaded["newest_in_history"],
        "left_out": left_out,
        "remaining": remaining,
        # The djlib set argument that brings the ``remaining`` songs, when one does.
        "next_spec": next_spec,
    }
    return body, warnings, summary


def continuation(workspace, through: str | None) -> str:
    """What brings the songs after a list of this Mac's oldest 1,000 that ends at ``through``.

    shazam:new continues from the newest Shazam imported, so it does when this list sets that
    mark (remember_import). After a newer import, e.g. the last 30 days first, it would find
    nothing new: the list that starts on this list's last day does (a few songs of that day
    come again).
    """
    mark = (shazam_history.last_import(workspace) or {}).get("newest_shazamed_at")
    if not through or not mark or datetime.fromisoformat(through) >= datetime.fromisoformat(mark):
        return "shazam:new"
    return f"shazam:{shazam_history.local_time(through)[:10]}"


def list_result(
    source: str | None = None, since: str | None = None, limit: int = 50, workspace=None
) -> dict:
    """The newest ``limit`` Shazams (newest first) since ``since``, with the total.

    ``source`` and ``since`` are as in ``shazam_history.load``; ``spec`` is the matching
    ``djlib set`` argument, or None when there is none: set takes a file or a date, not both.
    """
    loaded = shazam_history.load(source, since, workspace)
    records = loaded["records"]
    spec = f"shazam:{source or since}" if source or since else "shazam"

    def latest(item: dict) -> str | None:
        return item.get("last_shazamed_at") or item["shazamed_at"]

    # Newest first by the latest Shazam, so last night's re-Shazam is on top; songs without
    # a time go last, in the order of the file. (A set keeps the order you first heard them.)
    newest_first = sorted(
        records, key=lambda item: (latest(item) is not None, latest(item) or ""), reverse=True
    )
    whole = source is None and since is None and records
    return {
        "spec": None if source and since else spec,
        # For "my recent Shazams": spec "shazam" is the whole history, oldest first.
        "recent_spec": (
            f"shazam:{shazam_history.recent_since(shazam_history.newest(records))[0]}"
            if whole
            else None
        ),
        "query": {"since": since, "from": source},
        "songs": len(records),
        "songs_in_history": loaded["total"],
        "since": loaded["since"],
        "new": loaded["new"],
        "sources": loaded["sources"],
        "problems": loaded["problems"],
        "newest_shazamed_at": shazam_history.newest(records),
        "records": newest_first[:limit],
    }


def register_shazam(app, client, emit, handled, panel=None):
    shazam = typer.Typer(
        help="Songs you Shazamed: where they are and what's there.", no_args_is_help=True
    )
    app.add_typer(shazam, name="shazam", rich_help_panel=panel)

    @shazam.command("status")
    @handled
    def status(ctx: typer.Context) -> None:
        """Where your Shazam history is, and how many songs it has.

        Shows how to turn on iCloud sync for Shazam when nothing is found.
        """
        emit(envelope(shazam_history.status(ctx.obj)))

    @shazam.command("list")
    @handled
    def list_command(
        ctx: typer.Context,
        since: str | None = typer.Option(
            None, "--since", help="Only songs Shazamed since this date (2026-10-01), or new."
        ),
        source: str | None = typer.Option(
            None,
            "--from",
            help="apple-music, or an iPhone backup folder, ShazamDataModel.sqlite or CSV.",
        ),
        limit: int = typer.Option(50, min=1, max=MAX_SONGS, help="Show at most this many."),
    ) -> None:
        """Show the songs you Shazamed, newest first.

        Load them into a set with djlib set shazam (or shazam:new, shazam:2026-10-01).
        """
        if source is not None:
            parsed, _ = shazam_history.parse_spec(f"shazam:{source}")
            # --from never takes new or a date, so a folder named like one is meant here.
            if parsed is None and shazam_history.exists(Path(source).expanduser()):
                parsed = source
            if parsed is None:
                raise AppError("INPUT_INVALID", "--from takes apple-music or a file or folder.")
            source = parsed
        result = list_result(source, since, limit, ctx.obj)
        reply = envelope(result)
        if result["records"] and result["spec"] is None:
            reply["warnings"] = [
                "djlib set can't combine a file or Apple Music with a date, so no single "
                "command checks just these songs."
            ]
        emit(reply)
