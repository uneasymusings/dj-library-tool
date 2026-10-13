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
import unicodedata
from collections import Counter, defaultdict
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
from djlib.domain.contracts import artist_names, plain_labels, split_featured, without_artist_tags

WINDOW = 12.0
STRIDE = 30.0
BRIDGE_PROBES = 2
BRIDGE_SECONDS = 240.0
FOLD_SECONDS = 300.0
OFFSET_SLACK = 10.0
STRONG = 3
GAP_SECONDS = 90.0
ID_REACH = 180.0
WEAK_REACH = 60.0
HINTS = 3
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


def _clean(value) -> str:
    """Text on one line without control characters ("Halo\\tExtended" → "Halo Extended")."""
    text = "".join(c for c in str(value or "") if c.isspace() or unicodedata.category(c) != "Cc")
    return " ".join(text.split())


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


def _likeness(first: Song, second: Song) -> float:
    """How surely two labels name the same song in any version (see SAME, CLOSE, JOIN)."""
    if not first.core or not second.core:
        return 0.0
    shared = first.names & second.names
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
    """Listening windows by distinct time; several probes (providers, retries) share one."""

    times: list[float]
    failed: list[int]  # failed[n]: windows before the nth where every probe failed
    stride: float

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
    times = sorted({probe["at"] for probe in timeline})
    answered = {probe["at"] for probe in timeline if not probe.get("error")}
    failed = [0]
    for at in times:
        failed.append(failed[-1] + (at not in answered))
    steps = [b - a for a, b in pairwise(times)]
    return _Windows(times, failed, stride or (statistics.median(steps) if steps else STRIDE))


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
        mine = [hit for hit in counted if hit["provider"] == provider]
        providers.append(
            {
                "provider": provider,
                "hits": len({hit["at"] for hit in mine}),
                "at": min(hit["at"] for hit in mine),
                "until": max(hit["until"] for hit in mine),
            }
        )
    hits = len(windows)
    return {
        "start": windows[0],
        "end": max(hit["until"] for hit in counted),
        "hits": hits,
        "inconsistent": len(run.hits) - len(counted),
        "strength": "strong" if hits >= STRONG else "medium" if hits == 2 else "weak",
        "artist": best[0],
        "title": best[1],
        "version": best[2],
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
    number = {at: index for index, at in enumerate(windows.times)}
    runs: list[_Run] = []
    for probe in timeline:
        if (hit := _hit(probe, window)) is None:
            continue
        index = number[hit["at"]]
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
    same time (two providers, a retry) are one window. Windows whose song offset disagrees
    with the rest do not count towards its strength: "strong" (3+ windows), "medium" (2) or
    "weak" (1).
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
    items, skipped = parse_tracklist(text, source_url)
    entries = [item.model_dump() for item in items]
    left_out = {number for number, *_ in skipped}
    lines = [raw for number, raw in enumerate(text.splitlines(), 1) if number not in left_out]
    lines = [raw for raw in lines if _read_line(raw)]
    if len(lines) == len(entries):
        for entry, raw in zip(entries, lines, strict=True):
            entry["timestamp"] = entry["timestamp"] or _line_time(raw)
    left = [{"line": number, "text": raw, "reason": reason} for number, raw, reason in skipped]
    return {"source": source, "entries": entries, "skipped": left}


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
        # Two listings' IDs at about the same time are the same ID.
        near = entry["seconds"] is not None and any(
            text["kind"] == "unknown"
            and text["seconds"] is not None
            and abs(text["seconds"] - entry["seconds"]) <= GAP_SECONDS
            for text in slot.texts
        )
        return JOIN if near and slot.kind == "unknown" else 0.0
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
    """Times for listed songs: the heard time, the listed time, or one between neighbours."""
    for slot in seq:
        if slot.clusters:
            slot.time = min(cluster["start"] for cluster in slot.clusters)
        else:
            slot.time = next((t["seconds"] for t in slot.texts if t["seconds"] is not None), None)
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


def _stretches(
    slots: list[_Slot], total: float | None, weak: list[dict], unplaced: list[dict]
) -> list[_Slot]:
    """The slots in time order, with an ID wherever nothing was heard or listed for
    GAP_SECONDS or more after a heard song (or the start of the set).

    A listed song plays for as long as its listing says, so no stretch follows one. Songs
    heard once go to the nearest stretch as candidates, or to ``unplaced``.
    """
    found: list[_Slot] = []
    edge: float | None = 0.0
    for slot in [*_ordered(slots), None]:
        until = slot.time if slot else total
        if edge is not None and until is not None and until - edge >= GAP_SECONDS:
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
    return found


def _merge(
    clusters: list[dict], lists: list[tuple[str, list[dict]]], total, unplaced
) -> list[_Slot]:
    seq = [_Slot(clusters=[cluster], time=cluster["start"]) for cluster in clusters]
    for name, entries in lists:
        seq = _place(seq, name, entries)
    _interpolate(seq, total)
    _fill_ids(seq)
    anchors = [i for i, slot in enumerate(seq) if slot.clusters and slot.texts]
    weak: list[dict] = []
    for left, right in pairwise([-1, *anchors, len(seq)]):
        _settle(seq, seq[left + 1 : right], total, weak)
    return _stretches([slot for slot in seq if slot.track], total, weak, unplaced)


def _recognized(clusters: list[dict], total: float | None, unplaced: list[dict]) -> list[_Slot]:
    """No listing: songs heard in two or more windows, and IDs where nothing was heard."""
    songs = [_Slot(clusters=[c], time=c["start"]) for c in clusters if c["strength"] != "weak"]
    weak = [cluster for cluster in clusters if cluster["strength"] == "weak"]
    return _stretches(songs, total, weak, unplaced)


# Tracks


def _status(slot: _Slot) -> str:
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
        return "confirmed" if agree or len(sources) > 1 else "likely"
    if sources:
        return "confirmed" if len(sources) > 1 else "weak" if sources == {"comments"} else "likely"
    return "confirmed" if any(c["strength"] == "strong" for c in slot.clusters) else "likely"


def _names(slot: _Slot) -> tuple[str | None, str | None, str]:
    if slot.kind == "unknown":
        return None, None, ""
    best = max(slot.clusters, key=lambda cluster: cluster["hits"]) if slot.clusters else None
    text = next((text for text in slot.texts if text["kind"] == "named"), None)
    if not text:
        return best["artist"], best["title"], best["version"]
    version = text["version"]
    # The listing names the song; the audio may know which version was played.
    if not version and best and best["version"]:
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


def _hints(comments: list[dict], start: float, end: float | None, named: list[Song]) -> list[dict]:
    after = min(max((end or start + 300) - start, 120), 600)
    found = []
    for hint in comment_hints(comments, start, after=after):
        heard = _song(hint["artist"], hint["title"])
        if any(_likeness(heard, song) >= SAME or heard.core == song.core for song in named):
            continue  # a song already in the tracklist, e.g. the one before the ID
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


def _tracks(slots: list[_Slot], total: float | None, comments: list[dict]) -> list[dict]:
    ordered = _ordered(slots)
    named = [song for slot in ordered if slot.kind == "named" for song in slot.songs()]
    tracks = []
    for position, slot in enumerate(ordered, 1):
        start = slot.time
        end = ordered[position].time if position < len(ordered) else total
        if start is None or (end is not None and end < start):
            end = None
        if slot.kind == "unknown" and comments and start is not None:
            slot.candidates += _hints(comments, start, end, named)
        artist, title, version = _names(slot)
        tracks.append(
            {
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
        )
    return tracks


def _coverage(tracks: list[dict], total: float | None) -> dict:
    named = [track for track in tracks if track["kind"] == "named"]
    gaps, edge, unknown = [], 0.0, False
    for track in tracks:
        if track["start"] is None:
            continue
        if track["kind"] == "unknown":
            unknown = True
            continue
        if track["start"] > edge and (unknown or track["start"] - edge >= GAP_SECONDS):
            gaps.append({"start": edge, "end": track["start"]})
        edge = max(edge, track["end"] if track["end"] is not None else track["start"])
        unknown = False
    if total is not None and total > edge and (unknown or total - edge >= GAP_SECONDS):
        gaps.append({"start": edge, "end": total})
    return {
        "duration": total,
        "seconds_identified": sum(
            track["end"] - track["start"]
            for track in named
            if track["start"] is not None and track["end"] is not None
        ),
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
        found.append(
            {
                "source": "audio",
                "providers": sorted({str(m.get("provider") or "audio") for m in matches}),
                "windows": len(probes),
                "matched": len(matches),
                "errors": sum(1 for probe in probes if probe.get("error")),
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
    there ("conflict", 3+ windows) or become tracks of their own. Stretches of 90 s or more
    where nothing was heard or listed become IDs.

    Returns ``{"tracks", "coverage", "sources", "unplaced"}``; ``unplaced`` holds songs heard
    once that fit no ID.
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
        slots = _recognized(clusters, total, unplaced)
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
        parts += [
            f"possibly {_candidate_label(c)} ({_provider(c['provider'])}, "
            f"{_windows(c['hits'], c['at'])})"
            for c in audio
        ]
        if fans:
            parts.append("listeners named " + "; ".join(_candidate_label(c) for c in fans))
    else:
        if places:
            parts.append(f"also in {places}" if heard else f"from {places}")
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
