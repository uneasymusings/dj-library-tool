"""Merge audio recognition of a set with its published tracklists into one timeline.

Pure functions over JSON-able data a background job already gathered: recognition probes
(one listening window each), text tracklists (description, chapters, 1001Tracklists …) and
listener comments. No network and no files. Audio says when each song plays and text gives
the nicer label. A song heard in a single window never becomes a track on its own; it can
only be a candidate for an ID.
"""

import functools
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise

from djlib.application.source_matching import comment_hints, fold
from djlib.application.tracklists import (
    FEATURING_LABEL,
    LIST_LABEL,
    NUMBER_ONLY,
    NUMBERING,
    PLAYED_WITH,
    TIMESTAMP,
    TRAILING_TIMESTAMP,
    VERSION_WORDS,
    parse_tracklist,
)
from djlib.domain.contracts import (
    artist_names,
    plain_labels,
    safe_text,
    split_featured,
    without_artist_tags,
)

WINDOW = 12.0
STRIDE = 30.0
BRIDGE_PROBES = 2
BRIDGE_SECONDS = 240.0
FOLD_SECONDS = 300.0
OFFSET_SLACK = 10.0
STRONG = 3
GAP_SECONDS = 90.0
# Nothing heard for less than a song's length is the song before ending (a breakdown, a long
# blend, an outro the recognizer can't lock on to), not a song of its own.
SONG_SECONDS = 180.0
ID_REACH = 180.0
WEAK_REACH = 60.0
HINTS = 3
# Tracks read from one listing: a six-hour set has about 150. Merging is quadratic in them.
MAX_LISTED = 400
# How surely two labels name the same song in any version: 1 same credits and title, 0.9
# shared credits, SAME one title inside the other with a credited name in common, CLOSE a
# near-identical title, JOIN the same title by other artists (a remix heard as its sample).
SAME = 0.8
CLOSE = 0.7
JOIN = 0.6
RANK = {"strong": 0, "medium": 1, "weak": 2}
# Shazam lists tracks from DJ-mix compilations as "Title (Remixer) [Mixed]".
MIXED = re.compile(r"\s*[\(\[]\s*(?:mixed|mix\s*cut|continuous(?:\s+mix)?)\s*[\)\]]", re.I)
CLOCK = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)$")
DIGITS = re.compile(r"\d+")
SOURCE_NAMES = {
    "description": "the description",
    "chapters": "the chapters",
    "1001tracklists": "1001Tracklists",
    "mixesdb": "MixesDB",
    "file": "the tracklist file",
    "comments": "a listener's tracklist",
}
PROVIDER_NAMES = {"shazam": "Shazam", "acrcloud": "ACRCloud", "audd": "AudD"}


def clock(seconds: float) -> str:
    """A time in the set: "06:00" under an hour, "1:02:03" after."""
    hours, rest = divmod(max(round(seconds), 0), 3600)
    minutes, rest = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes:02d}:{rest:02d}"


def clock_seconds(value) -> float | None:
    match = CLOCK.match(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


OPENERS = {")": "(", "]": "["}


def _clean(value) -> str:
    """Text on one line without control characters ("Halo\\tExtended" → "Halo Extended"),
    and without a closing bracket nothing opened ("Picture Picture]", a listing's typo)."""
    text = " ".join(safe_text(value).split())
    while text and text[-1] in OPENERS and text.count(text[-1]) > text.count(OPENERS[text[-1]]):
        text = text[:-1].rstrip()
    return text


# Labels


def _trailing(title: str) -> tuple[str, str] | None:
    """("Bad Love", "Antdot & Maz (BR) Remix") from "Bad Love (Antdot & Maz (BR) Remix)"."""
    title = title.rstrip()
    if not title.endswith((")", "]")):
        return None
    depth = 0
    for index in range(len(title) - 1, -1, -1):
        if title[index] in ")]":
            depth += 1
        elif title[index] in "([":
            depth -= 1
            if not depth:
                return title[:index].rstrip(), title[index + 1 : -1].strip()
    return None


def _dash_version(title: str) -> tuple[str, str]:
    head, separator, tail = title.rpartition(" - ")
    if separator and head.strip() and VERSION_WORDS.search(tail):
        return head.strip(), tail.strip()
    return title, ""


def display_label(artist: str, title: str, version: str = "") -> tuple[str, str, str]:
    """Artist, title and version as shown: "[Mixed]" dropped, a trailing mix name split off."""
    title = MIXED.sub("", title).strip() or title.strip()
    if not version.strip():
        part = _trailing(title)
        if part and part[0] and VERSION_WORDS.search(part[1]):
            if not FEATURING_LABEL.match(part[1]):
                title, version = part
        elif not part:
            title, version = _dash_version(title)
    title, version = plain_labels(title, version.strip())
    return artist.strip(), title.strip(), version.strip()


def _label(artist: str, title: str, version: str = "") -> str:
    return f"{artist} - {title} ({version})" if version else f"{artist} - {title}"


@dataclass(frozen=True)
class Song:
    names: frozenset[str]
    core: str
    text: str


@functools.lru_cache(maxsize=8192)
def _song(artist: str, title: str, version: str = "") -> Song:
    """Credited names and the bare title, ignoring versions, featuring and country tags."""
    title = without_artist_tags(MIXED.sub("", title))
    rest, featured = split_featured(title)
    while (part := _trailing(rest)) and part[0]:
        rest = part[0]
    rest = _dash_version(rest)[0]
    names = frozenset(filter(None, map(fold, artist_names(artist, *featured))))
    return Song(names, fold(rest), fold(f"{artist} {title} {version}"))


def _inside(needle: str, text: str) -> bool:
    return bool(needle) and f" {needle} " in f" {text} "


def _adds_names(short: str, long: str, names: frozenset[str]) -> bool:
    """Whether the longer title is the shorter one plus credited names ("antdot mirage")."""
    extra = f" {long} ".replace(f" {short} ", " ", 1).split()
    return _inside(short, long) and set(extra) <= {word for name in names for word in name.split()}


@functools.lru_cache(maxsize=65536)
def _same_name(one: str, other: str) -> bool:
    """One credited name spelled two ways: "armand val helden" and "armand van helden" (same
    words, nearly the same letters), or "robert glasper" and "robert glasper experiment" (a
    name of two or more words that starts the other)."""
    if one == other:
        return True
    short, long = sorted((one.split(), other.split()), key=len)
    if len(short) >= 2 and long[: len(short)] == short:
        return True
    return len(short) == len(long) and SequenceMatcher(None, one, other).ratio() >= 0.85


def _shared(first: frozenset[str], second: frozenset[str]) -> set[str]:
    """The names in ``first`` that ``second`` credits too, however either spells them."""
    return {name for name in first if any(_same_name(name, other) for other in second)}


def _likeness(first: Song, second: Song) -> float:
    """How surely two labels name the same song in any version (see SAME, CLOSE, JOIN)."""
    if not first.core or not second.core:
        return 0.0
    shared = first.names & second.names or _shared(first.names, second.names)
    if first.core == second.core:
        return 1.0 if first.names == second.names else 0.9 if shared else JOIN
    credited = (
        shared
        or any(_inside(name, second.text) for name in first.names)
        or any(_inside(name, first.text) for name in second.names)
    )
    # "Stif Mayer - Antdot Mirage (Stif Mayer) [Mixed]" is "Antdot - Mirage"; "Bad Love" is
    # not "Love", even when Antdot remixed it.
    names = first.names | second.names
    if credited and (
        _adds_names(first.core, second.core, names) or _adds_names(second.core, first.core, names)
    ):
        return SAME
    if (
        shared
        and DIGITS.findall(first.core) == DIGITS.findall(second.core)
        and SequenceMatcher(None, first.core, second.core).ratio() >= 0.85
    ):
        return CLOSE
    return 0.0


# Audio segments


def _hit(probe: dict, window: float) -> dict | None:
    match = probe.get("match")
    if probe.get("error") or not isinstance(match, dict):
        return None
    artist, title = (_clean(match.get(key)) for key in ("artist", "title"))
    if not artist or not title:
        return None
    return {
        "at": probe["at"],
        "until": probe["at"] + (_number(probe.get("window")) or window),
        "provider": str(match.get("provider") or "audio"),
        "track_id": str(match.get("track_id") or "") or None,
        "isrc": str(match.get("isrc") or "").upper() or None,
        "url": match["url"] if isinstance(match.get("url"), str) else None,
        "artist": artist,
        "title": title,
        "offset": _number(match.get("song_offset")),
        "skew": _number(match.get("skew")),
        "consistent": True,
    }


@dataclass(eq=False)
class _Run:
    hits: list[dict]
    first: int  # windows, counted by distinct time
    last: int
    interruptions: list[dict] = field(default_factory=list)

    def joins(self, others: list[dict], least: float = JOIN) -> bool:
        ids = {(hit["provider"], hit["track_id"]) for hit in self.hits if hit["track_id"]}
        isrcs = {hit["isrc"] for hit in self.hits if hit["isrc"]}
        labels = {_song(hit["artist"], hit["title"]) for hit in self.hits}
        for other in others:
            if (other["provider"], other["track_id"]) in ids or other["isrc"] in isrcs:
                return True
            heard = _song(other["artist"], other["title"])
            if any(_likeness(label, heard) >= least for label in labels):
                return True
        return False


@dataclass(frozen=True)
class _Windows:
    """Listening windows, one per stride; several probes (providers, retries) share one."""

    times: list[float]
    failed: list[int]  # failed[n]: windows before the nth where every probe failed
    stride: float
    number: dict[float, int] = field(default_factory=dict)  # probe time → its window
    counts: dict[float, bool] = field(default_factory=dict)  # whether it counts as a hearing

    def close(self, earlier: int, later: int, skipped: int) -> bool:
        """At most ``skipped`` windows heard between two windows, within BRIDGE_SECONDS.

        Failed windows say nothing, so they do not count; windows missing from the probes count
        as heard, one per stride.
        """
        seconds = self.times[later] - self.times[earlier]
        between = max(later - earlier - 1, round(seconds / self.stride) - 1)
        between -= self.failed[later] - self.failed[min(earlier + 1, later)]
        return seconds <= BRIDGE_SECONDS and between <= skipped


def _listening(timeline: list[dict], stride: float | None) -> _Windows:
    """Windows every ``stride`` seconds from the first probe.

    A probe between two windows (a second listen where songs change, or around a failed
    window) belongs to the window before it. It places a change more exactly, but counts as
    hearing a song only where that window's own probe failed: a song heard once stays heard
    once however closely its edges were checked.
    """
    times = sorted({probe["at"] for probe in timeline})
    steps = [b - a for a, b in pairwise(times)]
    stride = stride or (statistics.median(steps) if steps else STRIDE)
    origin = times[0] if times else 0.0
    slot = {at: math.floor((at - origin) / stride + 1e-6) for at in times}
    on_grid = {at: abs(at - origin - slot[at] * stride) < 0.5 for at in times}
    first: dict[int, float] = {}
    for at in times:
        first.setdefault(slot[at], at)
    order = {key: index for index, key in enumerate(first)}
    answered = {slot[probe["at"]] for probe in timeline if not probe.get("error")}
    regular = {
        slot[probe["at"]] for probe in timeline if not probe.get("error") and on_grid[probe["at"]]
    }
    failed = [0]
    for key in first:
        failed.append(failed[-1] + (key not in answered))
    number = {at: order[slot[at]] for at in times}
    counts = {at: on_grid[at] or slot[at] not in regular for at in times}
    return _Windows(list(first.values()), failed, stride, number, counts)


def _bridge(merged: list[_Run], run: _Run, windows: _Windows) -> bool:
    """Join ``run`` to an earlier segment of the same song across a short gap."""
    for back in range(len(merged) - 1, -1, -1):
        earlier = merged[back]
        if not windows.close(earlier.last, run.first, BRIDGE_PROBES):
            return False
        if earlier.joins(run.hits):
            for inside in merged[back + 1 :]:
                earlier.interruptions += [
                    {"at": hit["at"], "artist": hit["artist"], "title": hit["title"]}
                    for hit in inside.hits
                ] + inside.interruptions
            del merged[back + 1 :]
            earlier.hits += run.hits
            earlier.last = run.last
            earlier.interruptions += run.interruptions
            return True
    return False


def _check_offsets(hits: list[dict]) -> None:
    """Mark hits whose song offset puts the song's start somewhere else than the others do."""
    groups = defaultdict(list)
    for hit in hits:
        if hit["offset"] is not None:
            groups[(hit["provider"], hit["track_id"] or hit["title"])].append(hit)
    for group in groups.values():
        if len(group) < 2:
            continue
        middle = sorted(group, key=lambda hit: hit["at"] - hit["offset"])[(len(group) - 1) // 2]
        origin = middle["at"] - middle["offset"]
        for hit in group:
            # A pitched track runs faster or slower than the set by its skew.
            slack = OFFSET_SLACK + abs(hit["skew"] or 0) * abs(hit["at"] - middle["at"])
            hit["consistent"] = abs(hit["at"] - hit["offset"] - origin) <= slack


def _heard_in(hits: list[dict]) -> int:
    """Windows that heard the song; one at least, even when only a check between them did."""
    return max(len({hit["window"] for hit in hits if hit["window"] is not None}), 1)


def _cluster(run: _Run) -> dict:
    _check_offsets(run.hits)
    counted = [hit for hit in run.hits if hit["consistent"]] or run.hits
    windows = sorted({hit["at"] for hit in counted})
    every = Counter(display_label(hit["artist"], hit["title"]) for hit in run.hits)
    heard = Counter(display_label(hit["artist"], hit["title"]) for hit in counted)
    mixed = {display_label(h["artist"], h["title"]) for h in run.hits if MIXED.search(h["title"])}
    order = list(every)
    # A release beats a DJ-mix compilation's copy of it, then the label heard most.
    best = max(heard, key=lambda label: (label not in mixed, heard[label], -order.index(label)))
    ids: dict[tuple, dict] = {}
    for hit in run.hits:
        key = (hit["provider"], hit["track_id"] or hit["isrc"] or hit["artist"] + hit["title"])
        entry = ids.setdefault(
            key,
            {"provider": hit["provider"], "track_id": hit["track_id"], "isrc": None, "url": None},
        )
        entry["isrc"] = entry["isrc"] or hit["isrc"]
        entry["url"] = entry["url"] or hit["url"]
    providers = []
    for provider in dict.fromkeys(hit["provider"] for hit in counted):
        mine = [hit for hit in run.hits if hit["provider"] == provider]
        providers.append(
            {
                "provider": provider,
                "hits": _heard_in([hit for hit in counted if hit["provider"] == provider]),
                "at": min(hit["at"] for hit in mine),
                "until": max(hit["until"] for hit in mine),
            }
        )
    hits = _heard_in(counted)
    # A window out of line still heard the song (a loop, a repeated part): it places the song,
    # but only windows in line count towards its strength.
    return {
        "start": min(hit["at"] for hit in run.hits),
        "end": max(hit["until"] for hit in run.hits),
        "hits": hits,
        "inconsistent": len(run.hits) - len(counted),
        "strength": "strong" if hits >= STRONG else "medium" if hits == 2 else "weak",
        "artist": best[0],
        "title": best[1],
        "version": best[2],
        # Heard only as a DJ-mix compilation's copy, which credits the mix, not the song.
        "mixed": best in mixed,
        "labels": [
            {"artist": a, "title": t, "version": v, "hits": n}
            for (a, t, v), n in every.most_common()
        ],
        "ids": list(ids.values()),
        "providers": providers,
        "times": windows,
        "interruptions": run.interruptions,
    }


def _runs(probes: list[dict] | None, window: float, stride: float | None) -> list[_Run]:
    timeline = [
        {**probe, "at": at}
        for probe in probes or []
        if isinstance(probe, dict) and (at := _number(probe.get("at"))) is not None
    ]
    timeline.sort(key=lambda probe: probe["at"])
    windows = _listening(timeline, stride)
    runs: list[_Run] = []
    for probe in timeline:
        if (hit := _hit(probe, window)) is None:
            continue
        index = windows.number[hit["at"]]
        hit["window"] = index if windows.counts[hit["at"]] else None
        if runs and windows.close(runs[-1].last, index, 0) and runs[-1].joins([hit]):
            runs[-1].hits.append(hit)
            runs[-1].last = index
        else:
            runs.append(_Run([hit], index, index))
    merged: list[_Run] = []
    for run in runs:
        if not _bridge(merged, run, windows):
            merged.append(run)
    return merged


def segments(
    probes: list[dict] | None, *, window: float = WINDOW, stride: float | None = None
) -> list[dict]:
    """What the probes heard, as songs in set order.

    Neighbouring windows naming the same song (same provider ID or ISRC, or labels naming the
    same song in any version) form one segment; up to two windows of silence or other songs
    inside it are bridged, and windows where recognition failed do not count. Probes at the
    same time (two providers, a retry), or between two windows (``recognition.refine``), share
    one window; pass the first listen's ``stride`` when such probes are mixed in, or the
    spacing is guessed from them. Windows whose song offset disagrees with the rest (a loop,
    a repeated part) still say when the song played, but do not count towards its strength:
    "strong" (3+ windows), "medium" (2) or "weak" (1).
    """
    return [_cluster(run) for run in _runs(probes, window, stride)]


def _fold(runs: list[_Run]) -> list[_Run]:
    """The same song heard again within FOLD_SECONDS is still the same play.

    A breakdown the recognizer missed, or a stray window later on; unless another song was
    heard in two or more windows in between.
    """
    kept: list[_Run] = []
    for run in runs:
        owner = None
        for earlier in reversed(kept):
            if run.hits[0]["at"] - max(hit["until"] for hit in earlier.hits) > FOLD_SECONDS:
                break
            if earlier.joins(run.hits, SAME):
                owner = earlier
                break
            if _cluster(earlier)["strength"] != "weak":
                break
        if owner:
            owner.hits += run.hits
            owner.interruptions += run.interruptions
            owner.last = run.last
        else:
            kept.append(run)
    return kept


# Text tracklists


def _line_time(raw: str) -> str | None:
    line = NUMBERING.sub("", PLAYED_WITH.sub("", raw.strip(), count=1), count=1)
    if match := TIMESTAMP.match(line):
        return match.group(1)
    line = NUMBERING.sub("", line, count=1).strip()
    if (match := TRAILING_TIMESTAMP.search(line)) and match.start() > 0:
        return next(filter(None, match.groups()))
    return None


def _read_line(raw: str) -> bool:
    line = PLAYED_WITH.sub("", raw.strip(), count=1)
    return bool(line) and not (
        line.startswith(("#", "//")) or NUMBER_ONLY.match(line) or LIST_LABEL.match(line)
    )


def listed_tracklist(source: str, text: str, source_url: str | None = None) -> dict:
    """A ``listed`` tracklist (``{"source", "entries", "skipped"}``) from text, through
    ``parse_tracklist``.

    Entries are request items as dicts; named ones also keep the time written on their line
    ("[06:00] Antdot - Mirage"), which request items leave out. ``skipped`` lists the lines
    left out (``{"line", "text", "reason"}``).
    """
    text = safe_text(text, lines=True)
    items, skipped = parse_tracklist(text, source_url)
    entries = [item.model_dump() for item in items]
    left_out = {number for number, *_ in skipped}
    lines = [
        (number, raw)
        for number, raw in enumerate(text.splitlines(), 1)
        if number not in left_out and _read_line(raw)
    ]
    if len(lines) == len(entries):
        for entry, (_, raw) in zip(entries, lines, strict=True):
            entry["timestamp"] = entry["timestamp"] or _line_time(raw)
    left = [{"line": number, "text": raw, "reason": reason} for number, raw, reason in skipped]
    if len(entries) > MAX_LISTED:
        number, raw = lines[MAX_LISTED] if len(lines) == len(entries) else (0, "")
        reason = f"more than {MAX_LISTED} tracks; this one and the rest are left out"
        left.append({"line": number, "text": raw, "reason": reason})
        entries = entries[:MAX_LISTED]
    return {"source": source, "entries": entries, "skipped": left}


def mixesdb_listing(found: dict) -> dict:
    """A page ``sources.mixesdb.lookup`` found, as a ``listed`` tracklist that also keeps the
    page's address and whether its editors call the tracklist complete."""
    listed = listed_tracklist("mixesdb", found["text"], found["page_url"])
    return {**listed, "url": found["page_url"], "complete": bool(found.get("complete"))}


def _entries(listed: dict) -> tuple[str, list[dict]]:
    name = str(listed.get("source") or "file")
    entries = []
    for position, raw in enumerate(listed.get("entries") or [], 1):
        if not isinstance(raw, dict):
            continue
        stamp = raw.get("timestamp") if isinstance(raw.get("timestamp"), str) else None
        entry = {"position": position, "timestamp": stamp, "seconds": clock_seconds(stamp)}
        if raw.get("kind") == "unknown":
            entries.append({**entry, "kind": "unknown", "label": str(raw.get("label") or "ID")})
            continue
        artist, title = (_clean(raw.get(key)) for key in ("artist", "title"))
        if not artist or not title:
            continue
        artist, title, version = display_label(artist, title, _clean(raw.get("version")))
        entries.append(
            {
                **entry,
                "kind": "named",
                "artist": artist,
                "title": title,
                "version": version,
                "song": _song(artist, title, version),
            }
        )
    return name, entries


# Merging


@dataclass(eq=False)
class _Slot:
    kind: str = "named"
    clusters: list[dict] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)
    time: float | None = None
    until: float | None = None
    approx: bool = False
    conflict: bool = False
    candidates: list[dict] = field(default_factory=list)
    track: bool = True

    def songs(self) -> list[Song]:
        heard = [
            _song(label["artist"], label["title"], label["version"])
            for cluster in self.clusters
            for label in cluster["labels"]
        ]
        return heard + [text["song"] for text in self.texts if text["kind"] == "named"]

    def edge(self) -> float | None:
        """Where the next song can start: after the last window this one was heard in."""
        return max(c["end"] for c in self.clusters) if self.clusters else self.time


def _fit(entry: dict, slot: _Slot) -> float:
    if entry["kind"] == "unknown":
        # Two listings' IDs between the same songs are the same ID: at about the same time,
        # or untimed in either (MixesDB writes "[4?] ?" when nobody knows the minute).
        if slot.kind != "unknown" or not slot.texts:
            return 0.0
        times = [text["seconds"] for text in slot.texts if text["kind"] == "unknown"]
        if entry["seconds"] is None or all(at is None for at in times):
            return JOIN
        near = any(at is not None and abs(at - entry["seconds"]) <= FOLD_SECONDS for at in times)
        return JOIN if near else 0.0
    return max((_likeness(entry["song"], song) for song in slot.songs()), default=0.0)


def _tiebreak(entry: dict, slot: _Slot) -> float:
    """Between equally likely slots: the song heard longer, and nearer the listed time."""
    hits = min(sum(cluster["hits"] for cluster in slot.clusters), 10)
    at = slot.time
    if at is None:
        at = next((text["seconds"] for text in slot.texts if text["seconds"] is not None), None)
    if at is None or entry["seconds"] is None:
        return hits
    return hits - min(abs(at - entry["seconds"]), 600) / 60


def _align(scores: list[list[float]], ties: list[list[float]]) -> dict[int, int]:
    """Entry → slot pairs in order, with the most likeness in total (``JOIN`` at least), then
    the best ``ties`` in total."""
    rows, columns = len(scores), len(scores[0]) if scores else 0
    table = [[(0, 0.0)] * (columns + 1) for _ in range(rows + 1)]

    def pair(i: int, j: int) -> tuple[int, float]:
        likeness, tie = table[i][j]
        return likeness + round(scores[i][j] * 100), tie + ties[i][j]

    for i in range(rows):
        for j in range(columns):
            best = max(table[i][j + 1], table[i + 1][j])
            if scores[i][j] >= JOIN:
                best = max(best, pair(i, j))
            table[i + 1][j + 1] = best
    pairs, i, j = {}, rows, columns
    while i and j:
        if scores[i - 1][j - 1] >= JOIN and table[i][j] == pair(i - 1, j - 1):
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif table[i][j] == table[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs


def _place(seq: list[_Slot], name: str, entries: list[dict]) -> list[_Slot]:
    """Lay one listing over the timeline so far; returns the new timeline."""
    scores = [[_fit(entry, slot) for slot in seq] for entry in entries]
    ties = [[_tiebreak(entry, slot) for slot in seq] for entry in entries]
    pairs = _align(scores, ties)
    taken = set(pairs.values())
    moved = {}
    # Listed out of order: a sure match to a heard song still takes the heard song's time.
    for i, entry in enumerate(entries):
        if i in pairs or entry["kind"] != "named":
            continue
        options = [
            j
            for j, slot in enumerate(seq)
            if slot.clusters and j not in taken and scores[i][j] >= SAME
        ]
        if options:
            moved[i] = max(options, key=lambda j: (scores[i][j], ties[i][j]))
            taken.add(moved[i])
    after: dict[int, list[_Slot]] = defaultdict(list)
    cursor = -1
    for i, entry in enumerate(entries):
        text = {**entry, "source": name}
        if i in pairs or i in moved:
            j = pairs[i] if i in pairs else moved[i]
            seq[j].texts.append({**text, "moved": i in moved})
            cursor = pairs.get(i, cursor)
            continue
        after[cursor].append(_Slot(kind=entry["kind"], texts=[text]))
    return after[-1] + [slot for j, here in enumerate(seq) for slot in (here, *after[j])]


def _interpolate(seq: list[_Slot], total: float | None) -> None:
    """Times for listed songs: the heard time, the listed time, or one between neighbours.

    A heard song starts at its listed time when that is shortly before the first window that
    heard it and after the song before: windows come every stride, so the first one to hear a
    song is up to a stride late ("[00:00] Roots" first heard at 00:09).
    """
    edge: float | None = 0.0
    for slot in seq:
        listed = next((t["seconds"] for t in slot.texts if t["seconds"] is not None), None)
        if slot.clusters:
            slot.time = slot.clusters[0]["start"]
            fits = listed is not None and edge is not None and edge <= listed <= slot.time
            if fits and slot.time - listed <= STRIDE + WINDOW:
                slot.time = listed
        else:
            slot.time = listed
        edge = slot.edge()
    listed = [slot for slot in seq if slot.texts]
    index = 0
    while index < len(listed):
        if listed[index].time is not None:
            index += 1
            continue
        stop = index
        while stop < len(listed) and listed[stop].time is None:
            stop += 1
        left = listed[index - 1] if index else None
        right = listed[stop] if stop < len(listed) else None
        low = left.edge() if left else 0.0
        high = right.time if right else total
        # After a heard song (or at the very start) the next one starts where it ends.
        flush = left is None or bool(left.clusters)
        count = stop - index
        for step, slot in enumerate(listed[index:stop]):
            slot.approx = True
            if high is None or low is None:
                continue
            if high <= low:
                slot.time = low
            elif flush:
                slot.time = round(low + step * (high - low) / count, 1)
            else:
                slot.time = round(low + (step + 1) * (high - low) / (count + 1), 1)
        index = stop


def _listed_by(slot: _Slot, sources: set[str]) -> bool:
    return any(text["source"] in sources for text in slot.texts)


def _fill_ids(seq: list[_Slot]) -> None:
    """An ID in one listing where another listing names a song is that song."""
    for slot in [s for s in seq if s.kind == "unknown" and s.texts]:
        mine = {text["source"] for text in slot.texts}
        at = seq.index(slot)
        low = next((i for i in range(at - 1, -1, -1) if _listed_by(seq[i], mine)), -1)
        high = next((i for i in range(at + 1, len(seq)) if _listed_by(seq[i], mine)), len(seq))
        options = [
            other
            for other in seq[low + 1 : high]
            if other.kind == "named"
            and other.texts
            and not _listed_by(other, mine)
            and (
                slot.approx
                or other.approx
                or slot.time is None
                or other.time is None
                or abs(other.time - slot.time) <= ID_REACH
            )
        ]
        if len(options) == 1:
            options[0].texts += slot.texts
            seq.remove(slot)


def _heard(cluster: dict) -> dict:
    main = max(cluster["providers"], key=lambda provider: provider["hits"])
    return {
        "source": "audio",
        "artist": cluster["artist"],
        "title": cluster["title"],
        "version": cluster["version"],
        "provider": main["provider"],
        "hits": cluster["hits"],
        "at": cluster["start"],
        "until": cluster["end"],
        "strength": cluster["strength"],
    }


def _apart(slot: _Slot, at: float) -> float:
    """Seconds from ``at`` to the slot, or to its stretch for an ID found in the audio."""
    if slot.time is None:
        return math.inf
    return max(slot.time - at, at - (slot.until if slot.until is not None else slot.time), 0.0)


def _nearest(slots: list[_Slot], cluster: dict) -> _Slot | None:
    return min(slots, key=lambda slot: _apart(slot, cluster["start"]), default=None)


def _reach(seq: list[_Slot], slot: _Slot, total: float | None) -> tuple[float, float]:
    """The stretch an ID can fill: between the listed songs either side of it."""
    at = seq.index(slot)
    before = next((s for s in reversed(seq[:at]) if s.texts and s.time is not None), None)
    after = next((s for s in seq[at + 1 :] if s.texts and s.time is not None), None)
    low = before.edge() if before else 0.0
    high = after.time if after else total if total is not None else math.inf
    return low, high


def _claims(slot: _Slot, reach: tuple[float, float], cluster: dict, slack: float) -> bool:
    start = cluster["start"]
    if reach[0] - slack <= start <= reach[1] + slack:
        return True
    return not slot.approx and slot.time is not None and abs(start - slot.time) <= ID_REACH


def _closest(pair: tuple[_Slot, _Slot]) -> tuple:
    """A heard song and a listed slot: nearest first, then the song heard best."""
    heard, listed = pair
    cluster = heard.clusters[0]
    return _apart(listed, heard.time), RANK[cluster["strength"]], -cluster["hits"], heard.time


def _settle(seq: list[_Slot], gap: list[_Slot], total: float | None, weak: list[dict]) -> None:
    """Between two songs both heard and listed: which heard song fills which ID, which
    disagrees with a listed song, which stays a track of its own.

    Each ID takes the nearest song heard in its stretch; songs heard once that fit no ID go
    to ``weak``.
    """
    unknowns = [slot for slot in gap if slot.texts and slot.kind == "unknown"]
    named = [slot for slot in gap if slot.texts and slot.kind == "named"]
    heard = [slot for slot in gap if not slot.texts]
    songs = [slot for slot in heard if slot.clusters[0]["strength"] != "weak"]
    reach = {slot: _reach(seq, slot, total) for slot in unknowns}
    pairs = [
        (song, slot)
        for song in songs
        for slot in unknowns
        if _claims(slot, reach[slot], song.clusters[0], 0)
    ]
    # An ID listed with a time, and a song heard near that time, takes no song from further.
    held = {slot for song, slot in pairs if not slot.approx and _apart(slot, song.time) <= ID_REACH}
    filled = set()
    for song, slot in sorted(pairs, key=_closest):
        if not song.track or slot in filled:
            continue
        if slot in held and _apart(slot, song.time) > ID_REACH:
            continue
        cluster = song.clusters[0]
        slot.candidates.insert(0, _heard(cluster))
        filled.add(slot)
        song.track = False
        if slot.approx:
            slot.time, slot.approx = cluster["start"], False
    rivals = [
        (song, slot)
        for song in songs
        if song.track and song.clusters[0]["strength"] == "strong"
        for slot in named
        if slot.approx or slot.time is None or abs(slot.time - song.time) <= ID_REACH
    ]
    for song, rival in sorted(rivals, key=_closest):
        if song.conflict or rival.conflict:
            continue
        cluster = song.clusters[0]
        rival.conflict = song.conflict = True
        if rival.approx:
            rival.time = cluster["start"]
        rival.candidates.append(_heard(cluster))
        text = rival.texts[0]
        song.candidates.append(
            {
                "source": text["source"],
                "artist": text["artist"],
                "title": text["title"],
                "version": text["version"],
            }
        )
    for slot in heard:
        cluster = slot.clusters[0]
        if cluster["strength"] != "weak":
            continue
        slot.track = False
        near = [u for u in unknowns if _claims(u, reach[u], cluster, WEAK_REACH)]
        if owner := _nearest(near, cluster):
            owner.candidates.append(_heard(cluster))
        else:
            weak.append(cluster)


def _ordered(slots: list[_Slot]) -> list[_Slot]:
    """Slots by time; one without a time stays after the slot before it."""
    keyed, last = [], -math.inf
    for index, slot in enumerate(slots):
        last = slot.time if slot.time is not None else last
        keyed.append((last, index, slot))
    return [slot for *_, slot in sorted(keyed, key=lambda item: item[:2])]


def _agrees(slot: _Slot, cluster: dict, least: float) -> bool:
    """Whether a heard song names one of the slot's songs (SAME), or its sample (JOIN)."""
    heard = [_song(label["artist"], label["title"]) for label in cluster["labels"]]
    return any(_likeness(song, mine) >= least for song in slot.songs() for mine in heard)


def _echoes(slots: list[_Slot], cluster: dict) -> bool:
    """A song heard once that names the song just before or after it (its sample, another
    version of it) is that song playing, not another one."""
    timed = [slot for slot in _ordered(slots) if slot.time is not None and slot.songs()]
    before = [slot for slot in timed if slot.time <= cluster["start"]][-1:]
    after = [slot for slot in timed if slot.time > cluster["start"]][:1]
    return any(_agrees(slot, cluster, JOIN) for slot in before + after)


def _drop_echoes(seq: list[_Slot]) -> list[_Slot]:
    """The slots without songs heard once that only echo a neighbour (see ``_echoes``)."""
    tracks = [slot for slot in seq if slot.texts or slot.clusters[0]["strength"] != "weak"]
    return [slot for slot in seq if slot in tracks or not _echoes(tracks, slot.clusters[0])]


def _by_region(region: tuple[float, float], candidate: dict) -> tuple:
    """An ID's likely song first, then songs heard once while it plays, then the nearest."""
    start, end = region
    away = max(start - candidate["at"], candidate["at"] - end, 0.0)
    return candidate["strength"] == "weak", away, candidate["at"]


def _stretches(
    slots: list[_Slot],
    total: float | None,
    weak: list[dict],
    unplaced: list[dict],
    *,
    between: bool,
    opening: bool,
    named: Callable[[float, float, list[_Slot]], bool] | None = None,
) -> list[_Slot]:
    """The slots in time order, with an ID wherever nothing was heard for SONG_SECONDS or more
    after a heard song (``between``) or from the start of the set (``opening``), or for
    GAP_SECONDS or more where ``named(start, end, neighbours)`` says a listener named another
    song.

    A listed song plays for as long as its listing says, so no stretch follows one. Songs
    heard once go to the nearest stretch as candidates, or to ``unplaced``; an ID lists those
    heard while it plays first.
    """
    found: list[_Slot] = []
    edge: float | None = 0.0
    for slot in [*_ordered(slots), None]:
        until = slot.time if slot else total
        allowed = between if found else opening
        if allowed and edge is not None and until is not None and until - edge >= GAP_SECONDS:
            beside = found[-1:] + ([slot] if slot else [])
            if until - edge >= SONG_SECONDS or (named and named(edge, until, beside)):
                found.append(_Slot(kind="unknown", time=edge, until=until))
        if slot is None:
            break
        found.append(slot)
        edge = max(edge or 0.0, slot.edge()) if slot.clusters else None
    stretches = [slot for slot in found if slot.until is not None]
    for cluster in weak:
        near = [slot for slot in stretches if _apart(slot, cluster["start"]) <= WEAK_REACH]
        if owner := _nearest(near, cluster):
            owner.candidates.append(_heard(cluster))
        else:
            unplaced.append(_heard(cluster))
    for index, slot in enumerate(found):
        if slot.kind == "unknown" and slot.time is not None:
            end = next((s.time for s in found[index + 1 :] if s.time is not None), total)
            region = (slot.time, math.inf if end is None else end)
            slot.candidates.sort(key=functools.partial(_by_region, region))
    return found


def _rejoin(seq: list[_Slot]) -> list[_Slot]:
    """A listed song heard again soon before or after, even past another song, is one play.

    Those windows within FOLD_SECONDS become more evidence for it, not a second track; it
    keeps the time of the windows the listing matched.
    """
    anchors = [slot for slot in seq if slot.clusters and slot.texts]

    def apart(anchor: _Slot, cluster: dict) -> float:
        first = min(heard["start"] for heard in anchor.clusters)
        return max(cluster["start"] - anchor.edge(), first - cluster["end"], 0.0)

    kept = []
    for slot in seq:
        cluster = slot.clusters[0] if slot.clusters and not slot.texts else None
        if cluster:
            owners = [
                anchor
                for anchor in anchors
                if apart(anchor, cluster) <= FOLD_SECONDS and _agrees(anchor, cluster, SAME)
            ]
            if owners:
                min(owners, key=lambda anchor: apart(anchor, cluster)).clusters.append(cluster)
                continue
        kept.append(slot)
    return kept


def _merge(
    clusters: list[dict], lists: list[tuple[str, list[dict]]], total, unplaced
) -> list[_Slot]:
    seq = [_Slot(clusters=[cluster], time=cluster["start"]) for cluster in clusters]
    for name, entries in lists:
        seq = _place(seq, name, entries)
    seq = _rejoin(seq)
    _interpolate(seq, total)
    seq = _drop_echoes(seq)
    _fill_ids(seq)
    anchors = [i for i, slot in enumerate(seq) if slot.clusters and slot.texts]
    weak: list[dict] = []
    for left, right in pairwise([-1, *anchors, len(seq)]):
        _settle(seq, seq[left + 1 : right], total, weak)
    # The listing says what plays: a stretch nobody heard belongs to the track before it, and
    # only an opening the listing leaves out can be an ID.
    opens = any(
        entry["seconds"] is not None and entry["seconds"] < GAP_SECONDS
        for _, entries in lists
        for entry in entries
    )
    tracks = [slot for slot in seq if slot.track]
    return _stretches(tracks, total, weak, unplaced, between=False, opening=not opens)


def _listener_named(comments: list[dict]) -> Callable[[float, float, list[_Slot]], bool]:
    """Whether a listener named a song, other than the ones either side, inside a stretch
    nothing was heard in: "sebastiAn - arabest" at 06:38 in a 108-second stretch."""

    def named(start: float, end: float, neighbours: list[_Slot]) -> bool:
        songs = [song for slot in neighbours for song in slot.songs()]
        for hint in comment_hints(comments, start, before=0, after=end - start):
            at = hint.get("at_seconds")
            if at is None or not start <= at < end or _beside(hint, songs):
                continue
            heard = _song(hint["artist"], hint["title"])
            if all(_likeness(heard, song) < JOIN for song in songs):
                return True
        return False

    return named


def _recognized(
    clusters: list[dict], total: float | None, unplaced: list[dict], comments: list[dict]
) -> list[_Slot]:
    """No listing: songs heard in two or more windows, and IDs where nothing was heard for
    SONG_SECONDS or more, or for GAP_SECONDS or more where a listener named another song.

    A song heard again after a stretch where nothing else was heard is still the same play.
    """
    songs: list[_Slot] = []
    for cluster in clusters:
        if cluster["strength"] == "weak":
            continue
        if songs and _agrees(songs[-1], cluster, SAME):
            songs[-1].clusters.append(cluster)
        else:
            songs.append(_Slot(clusters=[cluster], time=cluster["start"]))
    weak = [c for c in clusters if c["strength"] == "weak" and not _echoes(songs, c)]
    named = _listener_named(comments) if comments else None
    return _stretches(songs, total, weak, unplaced, between=True, opening=True, named=named)


# Tracks


def _status(slot: _Slot) -> str:
    """``confirmed`` needs the audio: the song heard where a listing places it, or heard in
    STRONG windows. Listings alone are ``likely`` however many name the song, since they copy
    each other (a MixesDB page often repeats the upload's description)."""
    if slot.kind == "unknown":
        if any(c["source"] == "audio" and c["strength"] != "weak" for c in slot.candidates):
            return "likely"
        return "weak" if slot.candidates else "unidentified"
    if slot.conflict:
        return "conflict"
    sources = {text["source"] for text in slot.texts if text["kind"] == "named"}
    if sources and slot.clusters:
        heard = [
            _song(label["artist"], label["title"], label["version"])
            for cluster in slot.clusters
            for label in cluster["labels"]
        ]
        agree = any(
            _likeness(text["song"], song) >= SAME
            for text in slot.texts
            if text["kind"] == "named"
            for song in heard
        )
        return "confirmed" if agree else "likely"
    if sources:
        return "weak" if sources == {"comments"} else "likely"
    return "confirmed" if any(c["strength"] == "strong" for c in slot.clusters) else "likely"


def _repairs(text: dict, cluster: dict) -> bool:
    """Whether a song heard where a listing places it spells the credits the listing
    misspells: the same title, and the same names one for one, some written differently
    ("Armand Val Helden" listed, "Armand Van Helden" heard). A wrong match can't agree that
    closely, so one regular window is enough, as it often is when a timed tracklist spaces
    the windows a minute apart. Other or extra credits, windows that only checked gaps or a
    DJ-mix compilation's copy leave the listing's label alone."""
    if cluster["hits"] < 1 or cluster.get("mixed"):
        return False
    heard = _song(cluster["artist"], cluster["title"], cluster["version"])
    listed = text["song"]
    if heard.core != listed.core or heard.names == listed.names:
        return False
    return len(heard.names) == len(listed.names) and all(
        any(_same_name(name, other) for other in heard.names) for name in listed.names
    )


def _named_from(slot: _Slot) -> tuple[dict | None, dict | None, bool]:
    """The slot's best heard song, its first listed name, and whether the heard label is the
    one shown (a listing's misspelt credits, see ``_repairs``)."""
    best = max(slot.clusters, key=lambda cluster: cluster["hits"]) if slot.clusters else None
    text = next((text for text in slot.texts if text["kind"] == "named"), None)
    return best, text, bool(best and text and _repairs(text, best))


def _names(slot: _Slot) -> tuple[str | None, str | None, str]:
    if slot.kind == "unknown":
        return None, None, ""
    best, text, repaired = _named_from(slot)
    if not text:
        return best["artist"], best["title"], best["version"]
    if repaired:
        return best["artist"], best["title"], best["version"] or text["version"]
    version = text["version"]
    # The listing names the song; the audio may know which version was played, unless the
    # listed title already ends in one ("ATM Jam (Kaytranada Edition)").
    if not version and best and best["version"] and _trailing(text["title"]) is None:
        played = _song(best["artist"], best["title"], best["version"])
        if _likeness(text["song"], played) >= SAME:
            version = best["version"]
    return text["artist"], text["title"], version


def _evidence(slot: _Slot) -> list[dict]:
    found = [
        {
            "source": "audio",
            **provider,
            "artist": cluster["artist"],
            "title": cluster["title"],
            "version": cluster["version"],
            "strength": cluster["strength"],
        }
        for cluster in slot.clusters
        for provider in cluster["providers"]
    ]
    for text in slot.texts:
        label = text.get("label") or _label(text["artist"], text["title"], text["version"])
        found.append(
            {
                "source": text["source"],
                "kind": text["kind"],
                "position": text["position"],
                "label": label,
                "timestamp": text["timestamp"],
                **({"moved": True} if text.get("moved") else {}),
            }
        )
    return found


def _beside(hint: dict, neighbours: list[Song]) -> bool:
    """Whether a listener's suggestion for an ID names the song just before or after it: that
    song's title as either half, since listeners swap artist and title and misspell names
    ("picture picture - cyrus" for Sharam Jey & Sirus Hood - Picture Picture)."""
    halves = {_song("", hint.get("artist") or "").core, _song("", hint.get("title") or "").core}
    return any(song.core in halves for song in neighbours if song.core)


def names_neighbour(hint: dict, neighbours: list[tuple[str, str]]) -> bool:
    """``_beside`` for the (artist, title) of the songs either side of an ID."""
    return _beside(hint, [_song(artist or "", title or "") for artist, title in neighbours])


def _hints(
    comments: list[dict],
    start: float,
    end: float | None,
    named: list[Song],
    neighbours: list[Song],
) -> list[dict]:
    after = min(max((end or start + 300) - start, 120), 600)
    found = []
    for hint in comment_hints(comments, start, after=after):
        heard = _song(hint["artist"], hint["title"])
        if any(_likeness(heard, song) >= SAME or heard.core == song.core for song in named):
            continue  # a song already in the tracklist, e.g. the one before the ID
        if _beside(hint, neighbours):
            continue  # the song before or after the ID, however the listener wrote it
        found.append(
            {
                "source": "comments",
                "artist": hint["artist"],
                "title": hint["title"],
                "mentions": hint["mentions"],
                "likes": hint["likes"],
                "at": hint["at_seconds"],
                "evidence": hint["evidence"][:1],
            }
        )
        if len(found) == HINTS:
            break
    return found


def _also_named(slot: _Slot, shown: Song) -> dict | None:
    """The other spelling of a track heard and listed under different names: the listing's,
    when the audio's label is shown (``_repairs``), else the audio's when it credits other
    names or another title."""
    best, text, repaired = _named_from(slot)
    if not best or not text:
        return None
    if repaired:
        label = _label(text["artist"], text["title"], text["version"])
        return {"source": text["source"], "label": label}
    heard = _song(best["artist"], best["title"], best["version"])
    if heard.names == shown.names and heard.core == shown.core:
        return None
    main = max(best["providers"], key=lambda provider: provider["hits"])
    label = _label(best["artist"], best["title"], best["version"])
    return {"source": "audio", "provider": main["provider"], "label": label}


def _pace(ordered: list[_Slot], total: float | None) -> float | None:
    """How long this set's named songs play, in the middle (None with fewer than three)."""
    ends = [slot.time for slot in ordered[1:]] + [total]
    lengths = [
        end - slot.time
        for slot, end in zip(ordered, ends, strict=False)
        if slot.kind == "named" and slot.time is not None and end is not None and end > slot.time
    ]
    return statistics.median(lengths) if len(lengths) >= 3 else None


def _backed_first(candidates: list[dict], candidate: dict) -> tuple[bool, bool]:
    """An ID's candidates: a song heard in two or more windows first, then one heard once that
    a listener also named within a minute of it, then the rest as they were."""
    if candidate["source"] != "audio":
        return True, True
    strong = candidate["strength"] != "weak"
    song = _song(candidate["artist"], candidate["title"])
    backed = any(
        other["source"] == "comments"
        and other.get("at") is not None
        and abs(other["at"] - candidate["at"]) <= 60
        and _likeness(song, _song(other["artist"], other["title"])) >= SAME
        for other in candidates
    )
    return not strong, not backed


def _tracks(slots: list[_Slot], total: float | None, comments: list[dict]) -> list[dict]:
    ordered = _ordered(slots)
    named = [song for slot in ordered if slot.kind == "named" for song in slot.songs()]
    pace = _pace(ordered, total)
    tracks = []
    for position, slot in enumerate(ordered, 1):
        start = slot.time
        end = ordered[position].time if position < len(ordered) else total
        if start is None or (end is not None and end < start):
            end = None
        if slot.kind == "unknown" and comments and start is not None:
            beside = ordered[max(position - 2, 0) : position - 1] + ordered[position : position + 1]
            neighbours = [song for other in beside for song in other.songs()]
            slot.candidates += _hints(comments, start, end, named, neighbours)
            slot.candidates.sort(key=functools.partial(_backed_first, list(slot.candidates)))
        artist, title, version = _names(slot)
        track = {
            "position": position,
            "start": start,
            "end": end,
            "approx": slot.approx,
            "kind": slot.kind,
            "artist": artist,
            "title": title,
            "version": version,
            "status": _status(slot),
            "evidence": _evidence(slot),
            "candidates": slot.candidates,
        }
        if slot.kind == "named" and (also := _also_named(slot, _song(artist, title, version))):
            track["also_named"] = also
        # A long stretch nobody heard or listed likely holds several songs.
        stretch = slot.kind == "unknown" and not slot.texts and pace and start is not None
        if stretch and end and (songs := round((end - start) / pace)) >= 2:
            track["songs_estimate"] = songs
        tracks.append(track)
    return tracks


def _hearing(tracks: list[dict], total: float | None) -> tuple[float, list[dict]]:
    """Seconds where the audio heard the song a track names (or an ID's likely song), and the
    stretches of GAP_SECONDS or more where it heard none, whatever a listing names there."""
    spans = []
    for track in tracks:
        heard = [e for e in track["evidence"] if e["source"] == "audio"]
        # An ID's likely song was heard there too, though it is not the ID's name.
        heard += [
            c for c in track["candidates"] if c["source"] == "audio" and c["strength"] != "weak"
        ]
        spans += [(e["at"], e["until"]) for e in heard]
    seconds, gaps, edge = 0.0, [], 0.0
    for start, end in sorted(spans):
        end = min(end, total) if total is not None else end
        if start - edge >= GAP_SECONDS:
            gaps.append({"start": edge, "end": start})
        seconds += max(end - max(start, edge), 0.0)
        edge = max(edge, end)
    if total is not None and total - edge >= GAP_SECONDS:
        gaps.append({"start": edge, "end": total})
    return round(seconds, 1), gaps


def _coverage(tracks: list[dict], total: float | None) -> dict:
    named = [track for track in tracks if track["kind"] == "named"]
    heard, gaps = _hearing(tracks, total)
    return {
        "duration": total,
        "seconds_identified": sum(
            track["end"] - track["start"]
            for track in named
            if track["start"] is not None and track["end"] is not None
        ),
        "seconds_heard": heard,
        "tracks": len(tracks),
        "named": len(named),
        "unknown": len(tracks) - len(named),
        "from_audio": sum(any(e["source"] == "audio" for e in t["evidence"]) for t in named),
        "from_text": sum(any(e["source"] != "audio" for e in t["evidence"]) for t in tracks),
        "gaps": gaps,
    }


def _sources(probes, clusters, lists, tracks, comments) -> list[dict]:
    found = []
    probes = [probe for probe in probes or [] if isinstance(probe, dict)]
    if probes:
        matches = [
            p["match"] for p in probes if not p.get("error") and isinstance(p.get("match"), dict)
        ]
        # A window asked again after it failed counts once, as failed only if every try did.
        tries: dict[float, list[dict]] = defaultdict(list)
        for probe in probes:
            if (at := _number(probe.get("at"))) is not None:
                tries[round(at, 3)].append(probe)
        found.append(
            {
                "source": "audio",
                "providers": sorted({str(m.get("provider") or "audio") for m in matches}),
                "windows": len(tries),
                "matched": len(matches),
                "errors": sum(1 for found in tries.values() if all(p.get("error") for p in found)),
                "songs": sum(1 for cluster in clusters if cluster["strength"] != "weak"),
            }
        )
    heard = Counter(
        e["source"]
        for track in tracks
        if any(e["source"] == "audio" for e in track["evidence"])
        for e in track["evidence"]
    )
    for name, entries in lists:
        found.append(
            {
                "source": name,
                "entries": len(entries),
                "named": sum(1 for entry in entries if entry["kind"] == "named"),
                "unknown": sum(1 for entry in entries if entry["kind"] == "unknown"),
                "heard": heard[name],
            }
        )
    if comments:
        found.append(
            {
                "source": "comments",
                "comments": len(comments),
                "suggestions": sum(
                    1 for t in tracks for c in t["candidates"] if c["source"] == "comments"
                ),
            }
        )
    return found


def build_tracklist(
    *,
    duration: float | None = None,
    probes: list[dict] | None = None,
    listed: list[dict] | None = None,
    comments: list[dict] | None = None,
    window: float = WINDOW,
    stride: float | None = None,
) -> dict:
    """One tracklist for a set from what was heard, what was published and what fans wrote.

    Heard songs give the times; each listing is aligned to them in order, so a listed song
    takes the heard song's start and keeps its own nicer label. Listed songs nobody heard get
    a time between their neighbours (``approx``). Songs heard in 2+ windows that no listing
    names fill the nearest listed ID in their stretch ("likely"), disagree with a listed song
    there ("conflict", 3+ windows) or become tracks of their own. The listing says what plays,
    so a stretch where nothing was heard belongs to the track before it; only an opening of
    3 minutes or more that the listing leaves out becomes an ID. Without a listing, stretches
    of 3 minutes or more where nothing was heard become IDs, unless the song before them is
    heard again after them.

    Returns ``{"tracks", "coverage", "sources", "unplaced"}``; ``unplaced`` holds songs heard
    once that fit no ID. ``coverage`` keeps the seconds inside named tracks
    (``seconds_identified``) apart from those where the audio heard the song placed there
    (``seconds_heard``); ``gaps`` are the stretches of 90 s or more where it heard none.
    """
    clusters = [_cluster(run) for run in _fold(_runs(probes, window, stride))]
    total = _number(duration)
    if not total or total <= 0:
        total = max(
            (
                at + (_number(probe.get("window")) or window)
                for probe in probes or []
                if isinstance(probe, dict) and (at := _number(probe.get("at"))) is not None
            ),
            default=None,
        )
    lists = [_entries(source) for source in listed or [] if isinstance(source, dict)]
    lists = [(name, entries) for name, entries in lists if entries]
    comments = [comment for comment in comments or [] if isinstance(comment, dict)]
    unplaced: list[dict] = []
    if lists:
        slots = _merge(clusters, lists, total, unplaced)
    else:
        slots = _recognized(clusters, total, unplaced, comments)
    tracks = _tracks(slots, total, comments)
    return {
        "tracks": tracks,
        "coverage": _coverage(tracks, total),
        "sources": _sources(probes, clusters, lists, tracks, comments),
        "unplaced": unplaced,
    }


# Output


def _windows(hits: int, at: float) -> str:
    return f"{hits} windows from {clock(at)}" if hits != 1 else f"1 window at {clock(at)}"


def _provider(name: str) -> str:
    return PROVIDER_NAMES.get(name, name.title())


def _candidate_label(candidate: dict) -> str:
    if not candidate.get("artist"):
        return candidate["title"]
    return _label(candidate["artist"], candidate["title"], candidate.get("version") or "")


def _places(track: dict, kind: str) -> str:
    names = dict.fromkeys(
        e["source"] for e in track["evidence"] if e["source"] != "audio" and e["kind"] == kind
    )
    return " and ".join(SOURCE_NAMES.get(name, name) for name in names)


def _notes(track: dict) -> str:
    heard = [e for e in track["evidence"] if e["source"] == "audio"]
    places, ids = _places(track, "named"), _places(track, "unknown")
    parts = [f"{_provider(e['provider'])}: {_windows(e['hits'], e['at'])}" for e in heard]
    if track["kind"] == "unknown":
        audio = [c for c in track["candidates"] if c["source"] == "audio"]
        fans = [c for c in track["candidates"] if c["source"] == "comments"]
        parts = []
        if audio and audio[0]["strength"] != "weak":
            top, audio = audio[0], audio[1:]
            parts.append(
                f"ID (likely {_candidate_label(top)}): {_provider(top['provider'])} heard it "
                f"in {_windows(top['hits'], top['at'])}"
            )
        if ids:
            parts.append(f"listed as an ID in {ids}")
        elif track["start"] is not None and track["end"] is not None:
            parts.append(f"not identified from {clock(track['start'])} to {clock(track['end'])}")
            if track.get("songs_estimate"):
                parts[-1] += f", about {track['songs_estimate']} songs at this set's pace"
        parts += [
            f"possibly {_candidate_label(c)} ({_provider(c['provider'])}, "
            f"{_windows(c['hits'], c['at'])})"
            for c in audio
        ]
        if fans:
            parts.append("listeners named " + "; ".join(_candidate_label(c) for c in fans))
    else:
        also = track.get("also_named") or {}
        if heard and places and track["status"] == "likely" and also.get("source") == "audio":
            # Heard and listed under other names: say what each side has, not "also in".
            verb = "list" if " and " in places else "lists"
            parts = [
                f"{places[:1].upper()}{places[1:]} {verb} it",
                *(
                    f"{_provider(e['provider'])} heard {also['label']} here "
                    f"({_windows(e['hits'], e['at'])})"
                    for e in heard
                ),
            ]
        elif places:
            parts.append(f"also in {places}" if heard else f"from {places}")
        if also.get("source") == "audio" and track["status"] != "likely":
            parts.append(f"{_provider(also['provider'])} named it {also['label']}")
        elif also and also.get("source") != "audio":
            where = SOURCE_NAMES.get(also["source"], also["source"])
            parts.append(f"listed in {where} as {also['label']}")
        if ids:
            parts.append(f"an ID in {ids}")
        if track["status"] == "conflict":
            for other in track["candidates"]:
                if other["source"] == "audio":
                    parts.append(
                        f"but {_provider(other['provider'])} heard {_candidate_label(other)} "
                        f"here ({_windows(other['hits'], other['at'])})"
                    )
                else:
                    where = SOURCE_NAMES.get(other["source"], other["source"])
                    parts.append(f"but {where} lists {_candidate_label(other)} here")
    if track["approx"] and track["start"] is not None:
        parts.append("the time is approximate")
    text = _clean("; ".join(parts))
    return (text[:1].upper() + text[1:])[:4000]


def track_notes(track: dict) -> str:
    """Why a track is in the tracklist, in a sentence or two (the notes of its request item)."""
    return _notes(track)


def request_items(tracklist: dict, source_url: str | None = None) -> list[dict]:
    """Request items (``RequestItem`` fields) in set order, with the evidence in ``notes``.

    IDs stay unknown items labelled "ID"; a likely identity goes in the notes only. An ID
    without a time is left out unless ``source_url`` says where to hear it.
    """
    items = []
    for track in tracklist.get("tracks") or []:
        stamp = clock(track["start"]) if track.get("start") is not None else None
        notes = _notes(track)
        if track["kind"] == "named":
            items.append(
                {
                    "kind": "named",
                    "artist": _clean(track["artist"])[:500],
                    "title": _clean(track["title"])[:1000],
                    "version": _clean(track.get("version"))[:300],
                    "timestamp": stamp,
                    "notes": notes,
                    "confidence": track["status"],
                }
            )
        elif stamp or source_url:
            items.append(
                {
                    "kind": "unknown",
                    "label": "ID",
                    "timestamp": stamp,
                    "source_url": source_url,
                    "notes": notes,
                    "confidence": track["status"],
                }
            )
    return items


def tracklist_text(tracklist: dict) -> str:
    """ "[mm:ss] Artist - Title (Version)" per line, "[mm:ss] ID - ID" for IDs."""
    lines = []
    for track in tracklist.get("tracks") or []:
        stamp = f"[{clock(track['start'])}] " if track.get("start") is not None else ""
        if track["kind"] == "unknown":
            lines.append(f"{stamp}ID - ID")
        else:
            lines.append(
                stamp + _label(track["artist"], track["title"], track.get("version") or "")
            )
    return "\n".join(lines)
