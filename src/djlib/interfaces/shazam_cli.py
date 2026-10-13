"""`djlib shazam …` and `djlib set shazam[:SPEC]`: the songs you Shazamed, as a set.

A Shazam history becomes a request list in the order you heard the songs, so it gets
owned/missing, downloads, a crate, rekordbox and USB like any other set.
"""

import hashlib
import json
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
    """ "Shazamed 2026-10-02 21:14 · Berlin" in this Mac's time zone."""
    parts = []
    if item.get("shazamed_at"):
        parts.append(f"Shazamed {shazam_history.local_time(item['shazamed_at'])}")
    if item.get("location"):
        parts.append(str(item["location"]))
    return " · ".join(parts)[:4000]


def describe(loaded: dict, songs: int) -> str:
    """ "Music Recognition on this Mac, 42 songs since 2026-10-01"."""
    where = " + ".join(source["label"] for source in loaded["sources"]) or "Shazam"
    text = f"{where}, {songs} song{'s' if songs != 1 else ''}"
    if loaded["since"]:
        text += f" since {shazam_history.local_time(loaded['since']).removesuffix(' 00:00')}"
    return text


def nothing_new(loaded: dict) -> AppError:
    """SHAZAM_NOTHING_NEW, naming the last import's list for ``shazam:new``."""
    start = shazam_history.local_time(loaded["since"])
    details = {"since": loaded["since"], "songs_in_history": loaded["total"]}
    again = "Shazam some songs, give them a few minutes to sync, then run this again."
    if not loaded["new"]:
        message = f"Nothing was Shazamed since {start}. {again}"
        return AppError("SHAZAM_NOTHING_NEW", message, details=details)
    last = loaded["last_import"] or {}
    details["last_import"] = {key: last.get(key) for key in ("request_id", "name", "imported_at")}
    message = f"Nothing new was Shazamed after {start} (the newest Shazam you imported). {again}"
    if last.get("request_id"):
        message += (
            f" Your last import is the list “{last.get('name') or 'Shazam'}”: "
            f"djlib requests get {short_id(last['request_id'])}"
        )
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
    title = (name or loaded["name"])[:300]
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
        warnings.append(
            f"This list has your first {MAX_SONGS} Shazams; run djlib set shazam:new "
            f"afterwards for the other {remaining}."
        )
    elif remaining:
        if all(item["shazamed_at"] for item in left):
            which = f"your {remaining} oldest Shazam{'s' if remaining != 1 else ''}"
        else:
            where = (
                shazam_history.LABELS["apple_music"]
                if source == shazam_history.APPLE_MUSIC
                else Path(source).name
            )
            which = f"the first {plural(remaining, 'song')} in {where}"
        warnings.append(f"A list holds {MAX_SONGS} songs, so this one leaves out {which}.")
    first_times = [item["shazamed_at"] for item in used if item["shazamed_at"]]
    # With songs left over, shazam:new continues after the last first Shazam in this list.
    newest = (
        max(first_times, default=None) if remaining and continues else shazam_history.newest(used)
    )
    summary = {
        "name": title,
        "description": describe(loaded, len(items)),
        "sources": loaded["sources"],
        "since": loaded["since"],
        "new": loaded["new"],
        "songs": len(items),
        "songs_in_history": loaded["total"],
        "first_shazamed_at": min(first_times, default=None),
        "newest_shazamed_at": newest,
        "left_out": left_out,
        "remaining": remaining,
    }
    return body, warnings, summary


def list_result(
    source: str | None = None, since: str | None = None, limit: int = 50, workspace=None
) -> dict:
    """The newest ``limit`` Shazams (newest first) since ``since``, with the total.

    ``source`` and ``since`` are as in ``shazam_history.load``; ``spec`` is the matching
    ``djlib set`` argument.
    """
    loaded = shazam_history.load(source, since, workspace)
    records = loaded["records"]
    # Newest first; songs without a time go last, in the order of the file.
    newest_first = sorted(
        records,
        key=lambda item: (item["shazamed_at"] is not None, item["shazamed_at"] or ""),
        reverse=True,
    )
    return {
        "spec": f"shazam:{source or since}" if source or since else "shazam",
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
        emit(envelope(list_result(source, since, limit, ctx.obj)))
