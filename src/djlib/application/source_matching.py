"""Rank provider search results and read fan evidence for one requested track.

Pure functions over data the caller already fetched: yt-dlp flat search entries, comments, and
a set's description and chapters. No network and no database. Scores are deterministic text
heuristics that order candidates for review; they are not acoustic identification.
"""

import functools
import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field

from djlib.application.tracklists import NUMBERING, SEPARATOR, TIMESTAMP, UNKNOWN
from djlib.domain.contracts import (
    artist_names,
    base_title,
    normalize,
    split_featured,
    version_markers,
    without_artist_tags,
)

MIN_SECONDS = 60  # SoundCloud Go+ previews arrive as 30-second entries.
MAX_TRACK_SECONDS = 15 * 60
DURATION_TOLERANCE = 8
SAME_LENGTH = 2
CONFIDENT_SCORE = 0.75
CONFIDENT_MARGIN = 0.1
PROBES = 3
MAX_ALTERNATES = 3
OFFICIAL = "official artist upload"
LABEL = "label upload"
VOUCHED = "same length as the artist's upload"
MAX_COMMENTS = 2000
MAX_COMMENT_CHARS = 5000
MAX_MENTIONS = 20_000
MAX_HINTS = 20
FAN_TRACKLIST_TIMES = 2
RANGE = re.compile(r"\s*(?:to|till|until|-|–|—|~)\s*", re.IGNORECASE)
MAX_EVIDENCE = 3
MAX_LINES = 2000

BASE_SCORE = 0.6
OFFICIAL_BONUS = 0.15
LABEL_BONUS = 0.06
AUDIO_BONUS = 0.05
DURATION_BONUS = 0.1
VIEWS_BONUS = 0.04
VERSION_PENALTY = 0.4
EXTENDED_PENALTY = 0.1
MISSING_PENALTY = 0.05
LYRICS_PENALTY = 0.03
VIDEO_PENALTY = 0.08


def _terms(text: str, separator: str = " ") -> frozenset[str]:
    return frozenset(text.split(separator))


ALIASES = {
    "rmx": "remix",
    "remixed": "remix",
    "remixes": "remix",
    "acappella": "acapella",
    "accapella": "acapella",
}
# Title words and phrases that mean a different recording from the one asked for.
VERSION_TERMS = {
    **{
        term: term
        for term in _terms(
            "remix bootleg edit vip rework flip dub live cover karaoke instrumental acapella "
            "slowed reverb nightcore 8d mashup acoustic extended mix lyrics"
        )
    },
    "unplugged": "live",
    "a cappella": "acapella",
    "sped up": "sped up",
    "speed up": "sped up",
    "mash up": "mashup",
    "lyric": "lyrics",
}
VENUES = _terms(
    "osheaga|coachella|glastonbury|tomorrowland|lollapalooza|primavera|sonar|dekmantel|"
    "awakenings|creamfields|bonnaroo|roskilde|pukkelpop|werchter|fuji rock|outside lands|"
    "parklife|letterman|fallon|kimmel|jools holland|kexp|live lounge|maida vale|cercle|"
    "colors show|audiotree|festival|concert|session|sessions",
    "|",
)
SET_PHRASES = _terms(
    "boiler room|live set|essential mix|tiny desk|full set|dj set|full concert|full album|"
    "full show|full mix",
    "|",
)
MIX_REQUEST = SET_PHRASES | _terms("dj mix|mixtape|megamix|continuous mix|mixed by|b2b", "|")
REASONS = {
    "live": "live recording",
    "venue": "festival or venue upload",
    "mashup": "mashup",
    "medley": "medley",
    "mix": "different mix",
    "extended": "extended mix not requested",
}
NOT_NAMES = {"mix", "version", "original", "the", "by"}
LABEL_WORDS = {"records", "recordings", "recs", "label"}
UPLOADER_SUFFIX = re.compile(r"(?:\s+(?:topic|vevo|official|music|tv|channel))+$")
AUDIO = re.compile(r"(?i)[\(\[]\s*(?:official\s+)?audio\s*[\)\]]|\bofficial\s+audio\b")
DATED_BRACKET = re.compile(r"[\(\[][^\)\]]*\b(?:19|20)\d{2}\b[^\)\]]*[\)\]]")
NOT_VENUE = re.compile(r"(?i)remaster|version|edition|mix|edit|anniversary|reissue|demo")
X_JOIN = re.compile(r"\S\s+[x×]\s+\S", re.IGNORECASE)
NAME_SPLIT = re.compile(
    r"\s+[x×]\s+|\s*[,;&]\s*|\s+(?:and|feat\.?|ft\.?|featuring|vs\.?)\s+", re.IGNORECASE
)
LETTER = re.compile(r"[^\W\d_]")
# Music videos often carry intros, skits or a different edit than the released track.
VIDEO = re.compile(r"\b(?:official\s+)?(?:music\s+)?video\b", re.IGNORECASE)


@functools.lru_cache(maxsize=8192)
def fold(value: str) -> str:
    """``normalize`` ignoring accents and apostrophes: "Björk's Café" becomes "bjorks cafe"."""
    value = re.sub(r"['’`´]", "", unicodedata.normalize("NFKD", value))
    return normalize("".join(c for c in value if not unicodedata.combining(c)))


def _words(value: str) -> list[str]:
    return [ALIASES.get(word, word) for word in fold(value).split()]


def _text(value: str) -> str:
    return f" {' '.join(_words(value))} "


def _has(text: str, phrase: str) -> bool:
    return bool(phrase.strip()) and f" {phrase.strip()} " in text


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _markers(title: str, credited: frozenset[str]) -> set[str]:
    """Version, performance and mashup markers in a title as written."""
    text = _text(title).replace(" original mix ", " ")
    found = {name for phrase, name in VERSION_TERMS.items() if _has(text, phrase)}
    found |= version_markers(text)
    dated = DATED_BRACKET.search(title) and not NOT_VENUE.search(title)
    if dated or any(_has(text, venue) for venue in VENUES):
        found.add("venue")
    parts = SEPARATOR.split(title, maxsplit=1)
    artist_part, song_part = parts if len(parts) == 2 else ("", title)
    if X_JOIN.search(song_part) or (
        X_JOIN.search(artist_part)
        and any(fold(name) not in credited for name in NAME_SPLIT.split(artist_part) if name)
    ):
        found.add("mashup")
    if re.search(r"\S\s*/\s*\S", base_title(split_featured(song_part)[0])):
        found.add("medley")
    if found - {"mix"}:
        found.discard("mix")
    return found


def _same_name(core: str, name: str) -> bool:
    return bool(core) and (core == name or core.replace(" ", "") == name.replace(" ", ""))


def _uploader_core(uploader: str) -> str:
    """ "Phoenix - Topic", "PhoenixVEVO" and "Phoenix Official" all become "phoenix"."""
    core = UPLOADER_SUFFIX.sub("", fold(uploader))
    return core.removesuffix("vevo").strip() or core


@dataclass(frozen=True)
class Wanted:
    primary: frozenset[str]
    credited: frozenset[str]
    label: str
    title_words: frozenset[str]
    version_words: frozenset[str]
    markers: frozenset[str]
    text: str
    is_mix: bool


def _wanted(requested: dict) -> Wanted:
    """The requested identity in matching form; a trailing "(... Remix)" counts as the version."""
    artist = without_artist_tags(str(requested.get("artist") or ""))
    title, title_featured = split_featured(without_artist_tags(str(requested.get("title") or "")))
    version = without_artist_tags(str(requested.get("version") or ""))
    base = base_title(title)
    if not version and base != title:
        trailing = title[len(base) :].strip(" ()[]")
        if _markers(trailing, frozenset()) - {"venue"}:
            version = trailing
    if fold(version) in {"original", "original mix", "original version"}:
        version = ""
    rest, inline = split_featured(artist)
    primary = frozenset(fold(name) for name in artist_names(rest))
    featured = {fold(name) for part in (*inline, *title_featured) for name in artist_names(part)}
    version_words = _words(version)
    required = set(version_words) - {"mix", "version", "the"} or set(version_words)
    remixer = " ".join(w for w in version_words if w not in VERSION_TERMS and w not in NOT_NAMES)
    credited = frozenset(primary | featured | ({remixer} if remixer else set()))
    text = _text(f"{artist} {title} {version}")
    return Wanted(
        primary=primary,
        credited=credited,
        label=fold(str(requested.get("label") or "")),
        title_words=frozenset(_words(base)),
        version_words=frozenset(required),
        markers=frozenset(_markers(f"{artist} - {title} ({version})", credited)),
        text=text,
        is_mix=any(_has(text, phrase) for phrase in MIX_REQUEST),
    )


@dataclass
class Assessed:
    entry: dict
    index: int
    score: float
    reasons: list[str]
    missing: bool
    versioned: bool
    official: bool
    label: bool
    duration: float | None


def _credits(entry: dict) -> str:
    """The publisher's artist credit (SoundCloud's ``artists``) in matching form."""
    artists = entry.get("artists")
    names = [name for name in artists if isinstance(name, str)] if isinstance(artists, list) else []
    return _text(" ".join(names[:10]))


def _assess(want: Wanted, entry: dict, index: int) -> Assessed | None:
    title = str(entry.get("title") or "")
    uploader = str(entry.get("uploader") or "")
    duration = _seconds(entry.get("duration"))
    title_text, text, credits = _text(title), _text(f"{title} {uploader}"), _credits(entry)
    words = set(text.split())
    if duration is not None and duration < MIN_SECONDS:
        return None
    if not want.is_mix and (
        (duration is not None and duration > MAX_TRACK_SECONDS)
        or any(_has(title_text, p) and not _has(want.text, p) for p in SET_PHRASES)
    ):
        return None
    if not want.title_words <= words or not want.version_words <= words:
        return None
    core = _uploader_core(uploader)
    official = any(_same_name(core, name) for name in want.credited)
    uploader_words = set(fold(uploader).split())
    label = not official and bool(
        (want.label and _same_name(core, want.label)) or LABEL_WORDS & uploader_words
    )
    named = {name for name in want.primary if _has(text, name) or _has(credits, name)}
    if not named and not official and not label:
        return None

    score, reasons, missing = BASE_SCORE, [], False
    if official:
        score += OFFICIAL_BONUS
        reasons.append(OFFICIAL)
    elif label:
        score += LABEL_BONUS
        reasons.append(LABEL)
    if not named and not official:
        score -= MISSING_PENALTY
        reasons.append("artist not in title")
        missing = True
    elif len(named) < len(want.primary) and not official:
        score -= MISSING_PENALTY
        reasons.append("credited artist missing")
        missing = True
    if entry.get("provider") == "youtube" and uploader.rstrip().endswith("- Topic") and official:
        score += AUDIO_BONUS
        reasons.append("YouTube Topic channel")
    if AUDIO.search(title):
        score += AUDIO_BONUS
        reasons.append("official audio")
    elif VIDEO.search(title):
        score -= VIDEO_PENALTY
        reasons.append("music video; may differ from the track")

    featured = {fold(name) for part in split_featured(title)[1] for name in artist_names(part)}
    extra = _markers(title, want.credited | featured) - want.markers
    if "live" in want.markers:
        extra.discard("venue")
    strong = sorted(extra - {"extended", "lyrics"})
    if strong:
        score -= VERSION_PENALTY
        reasons.extend(REASONS.get(marker, f"{marker} not requested") for marker in strong)
    if "extended" in extra:
        score -= EXTENDED_PENALTY
        reasons.append(REASONS["extended"])
    if "lyrics" in extra:
        score -= LYRICS_PENALTY
        reasons.append("lyrics upload")

    views = entry.get("view_count")
    if isinstance(views, int) and not isinstance(views, bool) and views > 0:
        score += VIEWS_BONUS * min(math.log10(views + 1) / 8, 1)
        if views >= 100_000:
            reasons.append("high view count")
    return Assessed(
        entry,
        index,
        score,
        reasons,
        missing,
        bool(strong) or "extended" in extra,
        official,
        label,
        duration,
    )


def _same_length(one: Assessed, other: Assessed) -> bool:
    return (
        one.duration is not None
        and other.duration is not None
        and abs(one.duration - other.duration) <= SAME_LENGTH
    )


def rank_sources(
    requested: dict, entries: list[dict], checked: dict[str, dict] | None = None
) -> list[dict]:
    """Plausible search entries for the requested track, best first.

    Each returned entry is a copy with ``score`` (0..1), ``confident``, short ``reasons``,
    ``other_version`` and ``available``. Entries missing the title, artist or requested version
    words, previews under a minute, over-long uploads and full sets are dropped; unrequested
    versions stay with a heavy penalty.

    ``checked`` maps URLs to availability probes (``available`` True, False or None). With it,
    uploads that are gone are left out (they still vouch for the recording's length) and only
    an upload that played can be confident.
    """
    want = _wanted(requested)
    assessed = [
        item
        for index, entry in enumerate(entries)
        if isinstance(entry, dict) and (item := _assess(want, entry, index)) is not None
    ]

    def available(item: Assessed) -> bool | None:
        return ((checked or {}).get(item.entry.get("url")) or {}).get("available")

    clean = [item for item in assessed if not item.versioned and item.duration is not None]
    for item in assessed:
        others = [other.duration for other in clean if other is not item]
        if (
            item.duration is not None
            and not item.versioned
            and others
            and abs(item.duration - statistics.median(others)) <= DURATION_TOLERANCE
        ):
            item.score += DURATION_BONUS
            item.reasons.append("duration matches others")
        if item.label and any(
            other.official and not other.versioned and _same_length(item, other) for other in clean
        ):
            item.reasons.append(VOUCHED)
        item.score = round(min(max(item.score, 0.0), 1.0), 3)

    def views(item: Assessed) -> int:
        count = item.entry.get("view_count")
        return count if isinstance(count, int) and not isinstance(count, bool) else 0

    def order(item: Assessed) -> tuple:
        entry = item.entry
        text = (str(entry.get(key) or "") for key in ("title", "uploader", "provider", "url"))
        return (-item.score, -views(item), *text, item.index)

    assessed.sort(key=order)
    kept = [item for item in assessed if available(item) is not False]
    confident = False
    if kept:
        best = kept[0]
        runner = kept[1] if len(kept) > 1 else None
        same_upload = (
            runner is not None
            and (runner.official or runner.label)
            and not runner.versioned
            and _same_length(best, runner)
        )
        # A label upload as long as the artist's own upload (even one that is gone) is that
        # recording, whether or not its title names the artist.
        vouched = VOUCHED in best.reasons
        confident = (
            (not best.missing or vouched)
            and not best.versioned
            and best.score >= CONFIDENT_SCORE
            and (checked is None or available(best) is True)
            and (
                runner is None
                or round(best.score - runner.score, 3) >= CONFIDENT_MARGIN
                or same_upload
                or vouched
            )
        )
    return [
        {
            **item.entry,
            "score": item.score,
            "confident": confident and position == 0,
            "reasons": item.reasons,
            "other_version": item.versioned,
            "available": available(item),
        }
        for position, item in enumerate(kept)
    ]


def worth_probing(ranked: list[dict], count: int = PROBES) -> list[str]:
    """URLs of the best few uploads of the requested version, for an availability check."""
    return [item["url"] for item in ranked if not item.get("other_version")][:count]


def alternates(ranked: list[dict], chosen: dict, limit: int = MAX_ALTERNATES) -> list[str]:
    """Other uploads to fetch when the chosen one turns out to be gone, best first.

    Only the requested version: the artist's or label's own uploads, or any upload of the
    same length as the chosen one.
    """
    length = _seconds(chosen.get("duration"))
    found = []
    for item in ranked:
        if item.get("url") == chosen.get("url") or item.get("other_version"):
            continue
        if item.get("available") is False or item.get("url") in found:
            continue
        duration = _seconds(item.get("duration"))
        same_length = None not in (length, duration) and abs(length - duration) <= SAME_LENGTH
        if same_length or {OFFICIAL, LABEL} & set(item.get("reasons") or []):
            found.append(item["url"])
    return found[:limit]


CLOCK = re.compile(r"(?<![\w:])@?(\d{1,2}(?::[0-5]\d){1,2})(?![\w:])")
CLAUSE = re.compile(r"\n|\s+[/|]\s+|[;!?]+|(?<=\w{4})\.(?:\s|$)")
LEADING = re.compile(r"^[\s\-–—:=>|.,)\]]+")
FILLER = re.compile(
    r"[:=\"“”]|@[\w.]+|\b(?:it['’]s|its|it|is|this|that['’]s|thats|that|was|track|tune|song|"
    r"id|yes|yeah|yep|yup|def|definitely|probably|prob|maybe|think|sure|called|playing|"
    r"played|i)\b",
    re.IGNORECASE,
)
TITLE_END = re.compile(
    r"\s+[-–—]\s+|,\s|\s+(?:is|was|goes|lol|lmao|haha|imo|btw)\b|\s+@|https?://"
    r"|\s+(?i:i think|i believe|i guess|i reckon|probably|maybe|for sure)\b"
)
# What is left of "Artist - Title at 4:20" once the time is cut out.
TITLE_TAIL = re.compile(r"\s+(?:at|@|around|from|near|~)\s*$", re.IGNORECASE)
CHAT = _terms(
    "is was part drop starts starting goes hits kicks moment section stretch transition bit"
)
PRAISE = _terms(
    "absolute absolutely amazing banger bangers beautiful best chills choon crazy damn fire goat "
    "goated good goosebumps great heater huge incredible insane love magic massive mmm omg "
    "perfect sick tune tunes unreal vibe vibes wow"
)
SMALL_TALK = _terms(
    "a all bro ever haha holy it its lol mate mix of pure set so song such that the this time "
    "track what yes"
)
QUESTION = re.compile(r"(?i)^(?:what|whats|who|anyone|does|any|track\s*id|song\s*id|id\b)")


def _clock_seconds(value: str) -> float:
    seconds = 0
    for part in value.split(":"):
        seconds = seconds * 60 + int(part)
    return float(seconds)


def _segments(text: str, start_time: float | None) -> list[tuple[str, float | None, bool]]:
    """Pieces of a comment with the time each belongs to and whether it is a tracklist entry.

    "2:41 - gunk / 12:00 - freedom 2" gives each title the time before it; "Bicep - Glue
    1:02:30" (text first, nothing after the last time) gives it the time after it. Text before
    the first time of a time-first list ("Tracklist:") is a heading, not an entry.
    """
    # (start, end, seconds) per moment; "12:00 to 14:00" and "12:00-14:00" are one moment.
    clocks: list[tuple[int, int, float]] = []
    for match in CLOCK.finditer(text):
        if clocks and RANGE.fullmatch(text[clocks[-1][1] : match.start()]):
            clocks[-1] = (clocks[-1][0], match.end(), clocks[-1][2])
            continue
        clocks.append((match.start(), match.end(), _clock_seconds(match.group(1))))
    if not clocks:
        return [(text, start_time, False)]
    times = [seconds for _, _, seconds in clocks]
    head, tail = text[: clocks[0][0]], text[clocks[-1][1] :]
    # A fan tracklist names several moments; "33:33 best drop ever" is a reaction, not a title.
    listing = len(clocks) >= FAN_TRACKLIST_TIMES
    if LETTER.search(head) and not LETTER.search(tail):
        starts = [0] + [clock[1] for clock in clocks[:-1]]
        return [
            (text[start : clock[0]], clock[2], listing)
            for start, clock in zip(starts, clocks, strict=True)
        ]
    ends = [clock[0] for clock in clocks[1:]] + [len(text)]
    pieces = [
        (text[clock[1] : end], clock[2], listing) for clock, end in zip(clocks, ends, strict=True)
    ]
    return ([(head, times[0], False)] if head.strip() else []) + pieces


def _clean_title(value: str) -> str:
    value = TITLE_END.split(value, maxsplit=1)[0]
    kept, depth, opened = [], 0, []
    for char in value:
        if unicodedata.category(char) in {"So", "Sk", "Cs", "Co"}:
            break
        if char in "([":
            depth += 1
            opened.append(len(kept))
        elif char in ")]":
            if not depth:
                break
            depth -= 1
            opened.pop()
        kept.append(char)
    if opened:
        kept = kept[: opened[0]]
    return TITLE_TAIL.sub("", "".join(kept).strip(" \t'\"“”‘’*~-–—.,:;")).strip()


def _clean_artist(value: str) -> str:
    cuts = list(FILLER.finditer(value))
    value = value[cuts[-1].end() :] if cuts else value
    # Trailing dots stay: "Fred again.." is a name.
    return value.strip(" \t'\"“”‘’*~-–—,:;([").lstrip(".").strip()


# Outside a fan tracklist, "Big love from Tokyo - Overmono rules" is a sentence with a dash.
PROSE = _terms(
    "i im i'm my me we our you your he she they this that it its is was are be been so just "
    "from in at of for with to on about what how when where who set sets times time day night "
    "evening morning weekend music performance crowd love boys girls guys everyone cheers "
    "respect thanks thank watching listening here there"
)
LOOSE_ENDS = _terms("the a an to of and or but he she it in on at for with my your")


def _sentence(artist: str, title: str) -> bool:
    """Whether a dash-separated pair from free text reads as prose rather than a credit."""
    names, words = _words(artist), _words(title)
    return (
        len(names) > 4
        or bool(set(names) & PROSE)
        or not words
        or words[-1] in LOOSE_ENDS
        or bool(set(words) & {"rules", "rule", "wait", "respect", "cheers", "thanks"})
    )


def _praise(value: str) -> bool:
    """ "what a tune", "absolute banger": praise words with small talk and nothing else."""
    words = set(_words(value))
    return bool(words & PRAISE) and words <= PRAISE | SMALL_TALK


def _plausible(value: str, max_words: int) -> bool:
    return (
        0 < len(value.split()) <= max_words
        and bool(LETTER.search(value))
        and not UNKNOWN.match(value)
        and "?" not in value
    )


def _mentions(clause: str, listing: bool) -> tuple[str, str] | None:
    clause = LEADING.sub("", clause)
    if match := SEPARATOR.search(clause):
        artist = _clean_artist(clause[: match.start()])
        title = _clean_title(clause[match.end() :])
        if (
            _plausible(artist, 6)
            and _plausible(title, 12)
            and not _praise(title)
            and fold(artist) not in {"track id", "song id"}
            and (listing or not _sentence(artist, title))
        ):
            return artist, title
        return None
    if not listing or QUESTION.match(clause) or "?" in clause:
        return None
    title = _clean_title(clause)
    if _plausible(title, 8) and not _praise(title) and not CHAT & set(_words(title)):
        return "", title
    return None


def _excerpt(text: str, needle: str, width: int = 100) -> str:
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    position = flat.casefold().find(" ".join(needle.split()).casefold())
    start = max(0, min(position - 30, len(flat) - width)) if position >= 0 else 0
    piece = flat[start : start + width].strip()
    return ("…" if start else "") + piece + ("…" if start + width < len(flat) else "")


@dataclass
class Hint:
    artist: str
    title: str
    first: int
    mentions: int = 0
    likes: int = 0
    evidence: list[str] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    comments: set[int] = field(default_factory=set)


def _supports(title: str, hint: Hint) -> bool:
    """An untimed reply ("yes Bicep - Glue edit") backing a timed claim of the same song."""
    mine, theirs = _words(title), _words(hint.title)
    return (
        bool(mine and theirs)
        and mine[0] == theirs[0]
        and (set(mine) <= set(theirs) or set(theirs) <= set(mine))
    )


def comment_hints(
    comments: list[dict], at_seconds: float | None, *, before: float = 90, after: float = 300
) -> list[dict]:
    """Identities fans wrote near ``at_seconds`` (anywhere when None), best first.

    Only "Artist - Title" strings, or titles in a timestamped fan tracklist, are read; nothing
    is inferred. Untimed replies count only when they back a timed identity near the moment.
    """
    timed, untimed = [], []
    for number, comment in enumerate(comments[:MAX_COMMENTS]):
        if len(timed) + len(untimed) >= MAX_MENTIONS:
            break
        if not isinstance(comment, dict) or not isinstance(comment.get("text"), str):
            continue
        text = comment["text"][:MAX_COMMENT_CHARS]
        likes = comment.get("like_count")
        likes = likes if isinstance(likes, int) and not isinstance(likes, bool) else 0
        for piece, seconds, listing in _segments(text, _seconds(comment.get("start_time"))):
            for clause in CLAUSE.split(piece):
                if found := _mentions(clause, listing):
                    mention = (number, *found, seconds, max(likes, 0), text)
                    (untimed if seconds is None else timed).append(mention)

    def near(seconds: float) -> bool:
        return at_seconds is None or at_seconds - before <= seconds <= at_seconds + after

    hints: dict[tuple[str, str], Hint] = {}
    by_artist: dict[str, list[Hint]] = {}

    def hint_for(key: tuple[str, str], artist: str, title: str, number: int) -> Hint:
        if key not in hints:
            hints[key] = Hint(artist, title, number)
            by_artist.setdefault(key[0], []).append(hints[key])
        return hints[key]

    def add(hint: Hint, number, title, seconds, likes, text):
        if number in hint.comments:
            return
        hint.comments.add(number)
        hint.mentions += 1
        hint.likes += likes
        if seconds is not None:
            hint.times.append(seconds)
        if len(hint.evidence) < MAX_EVIDENCE and (excerpt := _excerpt(text, title)) not in (
            hint.evidence
        ):
            hint.evidence.append(excerpt)

    for number, artist, title, seconds, likes, text in timed:
        if near(seconds):
            hint = hint_for((fold(artist), fold(title)), artist, title, number)
            add(hint, number, title, seconds, likes, text)
    for number, artist, title, seconds, likes, text in untimed:
        key = (fold(artist), fold(title))
        timed_peers = by_artist.get(key[0], []) if artist else []
        hint = hints.get(key) or next(
            (hint for hint in timed_peers if hint.times and _supports(title, hint)), None
        )
        if hint is None and at_seconds is None:
            hint = hint_for(key, artist, title, number)
        if hint is not None:
            add(hint, number, title, seconds, likes, text)

    def moment(hint: Hint) -> float | None:
        if not hint.times:
            return None
        if at_seconds is None:
            return min(hint.times)
        return min(hint.times, key=lambda seconds: (abs(seconds - at_seconds), seconds))

    def distance(hint: Hint) -> float:
        seconds = moment(hint)
        if seconds is None:
            return math.inf
        return seconds if at_seconds is None else abs(seconds - at_seconds)

    ranked = sorted(hints.values(), key=lambda h: (-h.mentions, -h.likes, distance(h), h.first))
    return [
        {
            "artist": hint.artist,
            "title": hint.title,
            "label": f"{hint.artist} - {hint.title}" if hint.artist else hint.title,
            "mentions": hint.mentions,
            "likes": hint.likes,
            "evidence": hint.evidence,
            "at_seconds": moment(hint),
        }
        for hint in ranked[:MAX_HINTS]
    ]


URL = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|ly|ee|co|fm|be|link|to)"
    r"\b(?:/\S*)?"
)
HASHTAG = re.compile(r"(?<!\w)#[^\W\d_]\w*")
HANDLE = re.compile(r"(?<![\w@])@[\w.]+")
HEADER = re.compile(r"(?i)^(?:full\s+)?(?:track\s*-?\s*list(?:ing)?|set\s*list)\s*[:\-–—]?\s*")
SOCIAL = re.compile(
    r"(?i)\b(?:instagram|insta|twitter|facebook|tiktok|youtube|soundcloud|spotify|bandcamp|"
    r"apple music|beatport|patreon|discord|twitch|bluesky|website)\b"
)
PROMO = re.compile(
    r"(?i)\b(?:subscribe|follow|out now|stream(?:ing)?|download|buy|pre-?order|pre-?save|"
    r"tickets?|merch|booking|contact|management|listen|available|copyright|all rights|support|"
    r"newsletter|tour dates|mastered|artwork|filmed|recorded|video by|directed|thanks|"
    r"thank you)\b|[©℗]"
)
TRAILING_TIME = re.compile(r"\s*[-–—|@]?\s*[\[(]?(\d{1,2}(?::[0-5]\d){1,2})[\])]?\s*$")
JUNK_TITLE = re.compile(r"(?i)^(?:intro|outro|start|end|chapter\s*\d+|untitled|break)$")


def _clock(seconds: float) -> str:
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _track_text(value: str) -> str:
    value = HANDLE.sub("", HASHTAG.sub("", URL.sub("", value)))
    value = NUMBERING.sub("", value.strip(" \t-–—|:•·"), count=1)
    return " ".join(value.split())


def _chapter_lines(chapters: list[dict]) -> list[tuple[float | None, str]]:
    found = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        title = NUMBERING.sub("", " ".join(str(chapter.get("title") or "").split()), count=1)
        if match := TIMESTAMP.match(title):
            title = title[match.end() :]
        title = _track_text(title)
        if LETTER.search(title) and not JUNK_TITLE.match(title):
            found.append((_seconds(chapter.get("start_time")), title))
    return sorted(found, key=lambda line: line[0] if line[0] is not None else math.inf)


def _description_lines(description: str) -> list[tuple[float | None, str]]:
    found = []
    for raw in description.splitlines()[:MAX_LINES]:
        line = NUMBERING.sub("", HEADER.sub("", " ".join(raw.split()), count=1), count=1)
        seconds = None
        if match := TIMESTAMP.match(line):
            seconds, line = _clock_seconds(match.group(1)), line[match.end() :]
        elif match := TRAILING_TIME.search(line):
            seconds, line = _clock_seconds(match.group(1)), line[: match.start()]
        if seconds is None and (
            URL.search(line) or SOCIAL.search(line) or PROMO.search(line) or HANDLE.search(line)
        ):
            continue
        line = _track_text(line)
        if not LETTER.search(line) or JUNK_TITLE.match(line):
            continue
        if seconds is not None or SEPARATOR.search(line):
            found.append((seconds, line))
    timed = [line for line in found if line[0] is not None]
    return timed if len(timed) >= 2 else found


def tracklist_from_source(description: str, chapters: list[dict]) -> str:
    """Tracklist text ("[mm:ss] Artist - Title" per line) from a set's chapters or description.

    Three or more chapters win; otherwise description lines that look like tracks are kept and
    links, hashtags, handles, headers and promo lines are dropped.
    """
    named = [c for c in chapters or [] if isinstance(c, dict) and str(c.get("title") or "")]
    lines = _chapter_lines(named) if len(named) >= 3 else []
    lines = lines or _description_lines(description or "") or _chapter_lines(named)
    return "\n".join(
        f"[{_clock(seconds)}] {text}" if seconds is not None else text for seconds, text in lines
    )
