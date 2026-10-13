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
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise

from djlib.application.source_matching import fold
from djlib.application.tracklists import FEATURING_LABEL, VERSION_WORDS
from djlib.domain.contracts import artist_names, plain_labels, split_featured, without_artist_tags

WINDOW = 12.0
STRIDE = 30.0
BRIDGE_PROBES = 2
BRIDGE_SECONDS = 240.0
OFFSET_SLACK = 10.0
STRONG = 3
# How surely two labels name the same song in any version: 1 same credits and title, 0.9
# shared credits, SAME one title inside the other with a credited name in common, CLOSE a
# near-identical title, JOIN the same title by other artists (a remix heard as its sample).
SAME = 0.8
CLOSE = 0.7
JOIN = 0.6
# Shazam lists tracks from DJ-mix compilations as "Title (Remixer) [Mixed]".
MIXED = re.compile(r"\s*[\(\[]\s*(?:mixed|mix\s*cut|continuous(?:\s+mix)?)\s*[\)\]]", re.I)
DIGITS = re.compile(r"\d+")


def clock(seconds: float) -> str:
    """A time in the set: "06:00" under an hour, "1:02:03" after."""
    hours, rest = divmod(max(round(seconds), 0), 3600)
    minutes, rest = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes:02d}:{rest:02d}"


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


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
    # "Stif Mayer - Antdot Mirage (Stif Mayer) [Mixed]" is "Antdot - Mirage".
    if credited and (_inside(first.core, second.core) or _inside(second.core, first.core)):
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
    artist, title = (str(match.get(key) or "").strip() for key in ("artist", "title"))
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
    first: int
    last: int
    interruptions: list[dict] = field(default_factory=list)

    def joins(self, others: list[dict]) -> bool:
        ids = {(hit["provider"], hit["track_id"]) for hit in self.hits if hit["track_id"]}
        isrcs = {hit["isrc"] for hit in self.hits if hit["isrc"]}
        labels = {_song(hit["artist"], hit["title"]) for hit in self.hits}
        for other in others:
            if (other["provider"], other["track_id"]) in ids or other["isrc"] in isrcs:
                return True
            heard = _song(other["artist"], other["title"])
            if any(_likeness(label, heard) >= JOIN for label in labels):
                return True
        return False


def _stride(timeline: list[dict]) -> float:
    steps = [b["at"] - a["at"] for a, b in pairwise(timeline) if b["at"] > a["at"]]
    return statistics.median(steps) if steps else STRIDE


def _bridge(merged: list[_Run], run: _Run, stride: float) -> bool:
    """Join ``run`` to an earlier segment of the same song across a short gap."""
    for back in range(len(merged) - 1, -1, -1):
        earlier = merged[back]
        seconds = run.hits[0]["at"] - earlier.hits[-1]["at"]
        skipped = max(run.first - earlier.last - 1, round(seconds / stride) - 1)
        if seconds > BRIDGE_SECONDS or skipped > BRIDGE_PROBES:
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


def segments(
    probes: list[dict] | None, *, window: float = WINDOW, stride: float | None = None
) -> list[dict]:
    """What the probes heard, as songs in set order.

    Neighbouring windows naming the same song (same provider ID or ISRC, or labels naming the
    same song in any version) form one segment; up to two windows of silence or other songs
    inside it are bridged. Windows whose song offset disagrees with the rest do not count
    towards its strength: "strong" (3+ windows), "medium" (2) or "weak" (1).
    """
    timeline = [
        {**probe, "at": at}
        for probe in probes or []
        if isinstance(probe, dict) and (at := _number(probe.get("at"))) is not None
    ]
    timeline.sort(key=lambda probe: probe["at"])
    stride = stride or _stride(timeline)
    runs: list[_Run] = []
    for index, probe in enumerate(timeline):
        if (hit := _hit(probe, window)) is None:
            continue
        if runs and runs[-1].last == index - 1 and runs[-1].joins([hit]):
            runs[-1].hits.append(hit)
            runs[-1].last = index
        else:
            runs.append(_Run([hit], index, index))
    merged: list[_Run] = []
    for run in runs:
        if not _bridge(merged, run, stride):
            merged.append(run)
    return [_cluster(run) for run in merged]
