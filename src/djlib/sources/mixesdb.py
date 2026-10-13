"""A set's tracklist from MixesDB, the wiki of DJ mixes, through its open MediaWiki API.

A page counts as the set only when it links the same upload, or when its title names the
same DJs and event and its date or length agrees with the upload's. A wrong tracklist is
worse than none, so a doubtful page, an outage or a slow answer gives no tracklist rather
than an error.
"""

import asyncio
import html
import logging
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from djlib import __version__
from djlib.domain.contracts import normalize
from djlib.domain.errors import AppError

logger = logging.getLogger(__name__)

API = "https://www.mixesdb.com/w/api.php"
PAGES = "https://www.mixesdb.com/w/"
USER_AGENT = f"djlib/{__version__} (+https://github.com/uneasymusings/dj-library-tool)"
MAX_REQUESTS = 4
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
DEADLINE = 20.0  # seconds for a whole lookup
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_PAGE_CHARS = 300_000
MAX_LINE_CHARS = 2000
MAX_NESTING = 5  # templates inside templates on a track line
RESULTS = 8
SLACK_SECONDS = 60.0
SLACK_SHARE = 0.02
PART_SHARE = 0.9  # a linked upload shorter than this share of the whole set is one part of it
SLACK_DAYS = 1  # a set after midnight may carry the next day's date
EVENT_SHARE = 0.75  # of the page's event words the upload must name
MIN_SCORE = 0.6
STRONG_SCORE = 1.0  # no date or length to agree: every key word on both sides
MARGIN = 0.1


def _terms(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Words that say nothing about which set this is.
BOILERPLATE = _terms(
    "dj djs set sets live livestream stream streamed full mix mixes mixed mixtape continuous "
    "hd hq 4k 8k uhd 1080p 720p official video audio recording recorded at the in on for from "
    "with by of a an and b2b b3b x vs presents pres feat ft featuring episode ep part pt vol "
    "volume tracklist free download premiere exclusive festival stage special edition"
)
YEAR = re.compile(r"^(?:19|20)\d\d$")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MONTH = (
    r"(?P<name>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
FOUR_DIGIT_YEAR = r"(?P<y>(?:19|20)\d\d)(?!\d)"
# "2019-08-30", "30.08.2019" or "08/30/2019", "16 Dec 2011", "December 16th, 2011".
DATES = tuple(
    re.compile(pattern, re.IGNORECASE | re.ASCII)
    for pattern in (
        r"(?<!\d)(?P<y>(?:19|20)\d\d)[-./](?P<m>\d\d?)[-./](?P<d>\d\d?)(?!\d)",
        rf"(?<!\d)(?P<a>\d\d?)[-./](?P<b>\d\d?)[-./]{FOUR_DIGIT_YEAR}",
        rf"(?<!\d)(?P<d>\d\d?)(?:st|nd|rd|th)?\s+{MONTH},?\s+{FOUR_DIGIT_YEAR}",
        rf"\b{MONTH}\s+(?P<d>\d\d?)(?:st|nd|rd|th)?,?\s+{FOUR_DIGIT_YEAR}",
    )
)
# Where the DJ's name ends: "Artist | Event", "Artist - Event", "Artist live at Event" …
LEAD_END = re.compile(r"\s*(?:[|｜/@:–—(\[]|\s-\s|\b(?:live|at|for|in|dj set)\b)\s*", re.IGNORECASE)
NAME_SPLIT = re.compile(
    r"\s*(?:,|&(?=\s)|\+|/|\bb[23]b\b|\bvs\.?(?=\s)|\band\b|\bx\b|\bfeat\.?(?=\s)|\bft\.?(?=\s)"
    r"|\bpres\.?(?=\s)|\bpresents\b)\s*",
    re.IGNORECASE,
)
PAGE_DATE = re.compile(r"^(\d{4})(?:-[\dX?]{2}){0,2}\s+-\s+", re.IGNORECASE)
UPLOADER_SUFFIX = re.compile(r"(?:\s+(?:topic|vevo|official|music|tv|channel))+$", re.IGNORECASE)

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_PATHS = ("embed", "v", "live", "shorts")
# Query parameters that name the video on other sites: facebook.com/watch?v=…, ?id=…
IDENTITY_PARAMS = ("v", "id")
LINK = re.compile(r"https?://[^\s|\[\]{}<>\"']+")

# Wiki text is untrusted: the patterns below keep their repeats bounded or apart, so none
# can backtrack for long (test_hostile_wikitext_parses_at_once).
COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
REF = re.compile(
    r"<ref\b[^<>/]*/>|<ref\b[^<>]*>(?:(?!<ref\b).)*?</ref\s*>", re.IGNORECASE | re.DOTALL
)
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
WIKI_LINK = re.compile(r"\[\[([^|\[\]]{0,300})(?:\|([^\[\]]{0,300}))?\]\]")
WEB_LINK = re.compile(r"\[(?:https?:)?//[^\s\[\]]+(?:\s+([^\[\]]*))?\]")
TAG = re.compile(r"<[^<>]{1,200}>")
LIST_MARK = re.compile(r"^[#*]+(?![#*:;])\s*")
LIST_TAG = re.compile(r"</?list\s*>", re.IGNORECASE)
PLAYED_OVER = re.compile(r"^\+\s+")
CUE = re.compile(r"^\[\s*([\d?]{1,3}(?::[\d?]{1,2}){0,2})\s*\]\s*")
GAP = re.compile(r"^(?:\.{2,}|…|-+)$")
PART = re.compile(
    r"^(?:cd|disc|disk|part|pt\.?|side|chapter|hour|set)\s*[\w.]{0,4}\s*:?$", re.IGNORECASE
)
TABLE_CLOCK = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?![\d:])")
PARTS_COLUMN = re.compile(r"^!\s*(?:pt|part)\b", re.IGNORECASE)
RADIO_SHOW = re.compile(r"\{\{\s*StandardShow", re.IGNORECASE)
SLOT = re.compile(r"\{\{\s*StandardShow(\d+)(h|min)\s*\}\}", re.IGNORECASE)
PLAYER = re.compile(r"\{\{\s*Player\s*\|([^{}]{0,5000})\}\}", re.IGNORECASE)
STATUS = re.compile(
    r"\[\[\s*Category\s*:\s*Tracklist\s*:\s*(complete|incomplete|none)\s*(?:\|[^\]]{0,300})?\]\]",
    re.IGNORECASE,
)


# Pages


def parse_page(wikitext: str) -> dict:
    """What a page says about its mix: tracks in order (``(time or None, label)``), length,
    player links, every link, and "complete", "incomplete" or "unknown" for its tracklist.

    ``lengths`` holds every length of the whole set its file tables list (a table of parts
    counts as their total), and ``duration_seconds`` the first of them, else a radio show's
    slot. ``radio_show`` says the page is an episode of a show ({{StandardShow1h}}), all of
    whose episodes share one length.
    """
    text = COMMENT.sub("", wikitext[:MAX_PAGE_CHARS])
    sections = _sections(text)
    details = sections.get("file details", "")
    lengths = _lengths(details)
    status = STATUS.search(text)
    players = [_link_end(link) for body in PLAYER.findall(text) for link in LINK.findall(body)]
    return {
        "tracks": _tracks(sections.get("tracklist", "")),
        "duration_seconds": lengths[0] if lengths else _slot(details),
        "lengths": lengths,
        "radio_show": bool(RADIO_SHOW.search(details)),
        "players": list(dict.fromkeys(players)),
        "links": list(dict.fromkeys(_link_end(link) for link in LINK.findall(text))),
        "tracklist": status.group(1).lower() if status else "unknown",
    }


def tracklist_text(tracks: list[tuple[str | None, str]]) -> str:
    """Numbered "1. [00:45] Artist - Title (Mix)" lines, as ``parse_tracklist`` reads them.

    The numbers keep every line a track, also an untimed one above the first timed line,
    which an unnumbered list would read as a heading. A label that starts like a number or
    a time ("#1 Crush - Garbage", "12:30 - Intro") loses that start when read back.
    """
    return "\n".join(
        f"{number}. [{time}] {label}" if time else f"{number}. {label}"
        for number, (time, label) in enumerate(tracks, 1)
    )


def page_url(title: str) -> str:
    return PAGES + quote(title.replace(" ", "_"), safe=";@$!*(),/~:")


def _sections(text: str) -> dict[str, str]:
    """Each top-level ("== Tracklist ==") section's body; deeper headings stay inside."""
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.splitlines():
        if (name := _section_name(line)) is not None:
            current = name
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def _section_name(line: str) -> str | None:
    line = line.strip()
    if not line.startswith("==") or line.startswith("===") or not line.endswith("="):
        return None
    return line.strip("=").strip().casefold() or None


def _tracks(section: str) -> list[tuple[str | None, str]]:
    """List items ("# [00:45] …") and the lines of a <list> block, in order. Headings
    ("=== CD1 ===", ";Part 2", "Part 2"), notes ("#:") and gaps ("...") are not tracks;
    "+ " (played over the track before) is left out of the label."""
    tracks = []
    listing = False
    section = LIST_TAG.sub(lambda tag: "\n</list>\n" if "/" in tag[0] else "\n<list>\n", section)
    for line in section.splitlines():
        line = line.strip()[:MAX_LINE_CHARS]
        if line in {"<list>", "</list>"}:
            listing = line == "<list>"
            continue
        mark = LIST_MARK.match(line)
        if not mark and not (listing and line and not line.startswith(("=", ";", ":"))):
            continue
        body = _plain(line[mark.end() :] if mark else line)
        seconds = None
        if cue := CUE.match(body):
            seconds, body = _cue_seconds(cue.group(1)), body[cue.end() :].strip()
        body = PLAYED_OVER.sub("", body, count=1)
        if body and not GAP.match(body) and not PART.match(body):
            tracks.append((seconds, body[:1000]))
    return [(_clock(seconds), label) for seconds, label in _in_order(tracks)]


def _in_order(tracks: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    """The tracks without times from the first one that goes back in time (each CD of a
    compilation starts again at 0), so that a merge by time keeps the page's order."""
    latest = 0
    for index, (seconds, _) in enumerate(tracks):
        if seconds is not None and seconds < latest:
            return tracks[:index] + [(None, label) for _, label in tracks[index:]]
        latest = max(latest, seconds or 0)
    return tracks


def _plain(value: str) -> str:
    """A track line without wiki markup: "'''[[Moodymann|KDJ]]'''<ref>…</ref>" is "KDJ"."""
    value = REF.sub("", value)
    for _ in range(MAX_NESTING):
        value, found = TEMPLATE.subn("", value)
        if not found:
            break
    value = WIKI_LINK.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), value)
    value = WEB_LINK.sub(lambda m: m.group(1) or "", value)
    value = TAG.sub(" ", value.replace("'''", "").replace("''", ""))
    value = html.unescape(value)
    # Control and format characters (such as a right-to-left override) can disguise a label.
    value = "".join(c for c in value if c.isspace() or unicodedata.category(c) not in {"Cc", "Cf"})
    return " ".join(value.split())


def _cue_seconds(value: str) -> int | None:
    """MixesDB writes "[00:45]", "[1:02:03]" or minutes alone ("[042]"); "?" is unknown."""
    if "?" in value:
        return None
    parts = [int(part) for part in value.split(":")]
    seconds = 0
    for part in parts if len(parts) > 1 else [parts[0], 0]:
        seconds = seconds * 60 + part
    return seconds


def _clock(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes:02d}:{rest:02d}"


def _lengths(section: str) -> list[float]:
    """The whole set's lengths in the "File details" tables: each recording's, or for a
    table of parts ("! Pt."), their total."""
    lengths: list[float] = []
    parted = False
    for line in section.splitlines():
        line = line.strip()
        parted = parted or bool(PARTS_COLUMN.match(line))
        if line.startswith(("|", "!")) and "://" not in line and (m := TABLE_CLOCK.search(line)):
            hours, minutes, seconds = (int(part or 0) for part in m.groups())
            if total := hours * 3600 + minutes * 60 + seconds:
                lengths.append(float(total))
    if parted and len(lengths) > 1:
        *parts, last = lengths
        return [last if _near(sum(parts), last) else sum(lengths)]
    return list(dict.fromkeys(lengths))


def _slot(section: str) -> float | None:
    """A radio show's slot ({{StandardShow2h}}), which is not the recording's length."""
    if show := SLOT.search(section):
        return float(int(show.group(1)) * (3600 if show.group(2).lower() == "h" else 60))
    return None


def _near(duration: float, length: float) -> bool:
    return abs(duration - length) <= max(SLACK_SECONDS, SLACK_SHARE * length)


def _link_end(link: str) -> str:
    return link.rstrip(".,;:!)")


# Links


def url_key(url: str | None) -> str | None:
    """One spelling per upload: youtu.be, watch?v= and embed links are "youtube:<id>"; other
    links lose their scheme, "www."/"m.", trailing slash and query ("soundcloud.com/a/b"),
    except a parameter that names the video ("facebook.com/watch?v=123")."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host:
        return None
    host = re.sub(r"^(?:www|m|mobile|music)\.", "", host)
    path = parts.path.rstrip("/")
    query = parse_qs(parts.query)
    if host == "youtu.be":
        video = path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "youtube-nocookie.com"}:
        segments = path.lstrip("/").split("/")
        if segments[0] == "watch":
            video = (query.get("v") or [""])[0]
        else:
            video = segments[1] if len(segments) > 1 and segments[0] in YOUTUBE_PATHS else ""
    else:
        names = (f"?{name}={query[name][0]}" for name in IDENTITY_PARAMS if query.get(name))
        return f"{host}{path.lower()}{next(names, '')}" if path else None
    return f"youtube:{video}" if YOUTUBE_ID.match(video) else None


def link_spellings(url: str | None) -> list[str]:
    """How to ask MixesDB for the pages that link an upload: a YouTube video by its key; any
    other link by its host and path as written, since MixesDB matches the spelling it stores
    (capitals and "www." included), then with "www." added or taken off."""
    key = url_key(url)
    if not key or key.startswith("youtube:"):
        return [key] if key else []
    parts = urlsplit(str(url).strip())
    host = re.sub(r"^(?:m|mobile)\.", "", (parts.hostname or "").lower())
    path = parts.path.rstrip("/")
    other = host.removeprefix("www.") if host.startswith("www.") else f"www.{host}"
    return [f"{host}{path}", f"{other}{path}"]


# Titles


def _words(value: str) -> list[str]:
    """Lower-case words without accents or apostrophes: "Böhmer's" is "bohmers"."""
    value = re.sub(r"['’`´]", "", unicodedata.normalize("NFKD", value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", value.casefold()).split()


def _key_words(words: list[str]) -> list[str]:
    return [w for w in dict.fromkeys(words) if w not in BOILERPLATE and not w.isdigit()]


def _search_words(value: str) -> list[str]:
    """Key words as written, accents kept (MixesDB's search folds them itself)."""
    return [w for w in dict.fromkeys(normalize(value).split()) if _key_words(_words(w))]


def _numbers(words: list[str]) -> set[int]:
    return {int(w) for w in words if w.isdecimal() and len(w) <= 4 and not YEAR.match(w)}


def _dates(text: str) -> tuple[set[date], str]:
    """The days a title names, and the title without them. "05/10/2013" may be 5 October
    or 10 May, so it gives both."""
    days: set[date] = set()
    for form in DATES:
        for match in form.finditer(text):
            days |= _days(match)
        text = form.sub(" ", text)
    return days, text


def _days(match: re.Match) -> set[date]:
    found = match.groupdict()
    if found.get("name"):
        orders = [(MONTHS.index(found["name"][:3].lower()) + 1, int(found["d"]))]
    elif found.get("a"):
        orders = [(int(found["b"]), int(found["a"])), (int(found["a"]), int(found["b"]))]
    else:
        orders = [(int(found["m"]), int(found["d"]))]
    days = set()
    for month, day in orders:
        with suppress(ValueError):
            days.add(date(int(found["y"]), month, day))
    return days


def _same_day(days: set[date], others: set[date]) -> bool | None:
    """Whether two titles name the same day, give or take SLACK_DAYS; None if one names none."""
    if not days or not others:
        return None
    return any(abs((one - other).days) <= SLACK_DAYS for one in days for other in others)


def _has(words: list[str], name: list[str]) -> bool:
    size = len(name)
    return any(words[i : i + size] == name for i in range(len(words) - size + 1))


@dataclass
class Wanted:
    """The upload a lookup is for."""

    title: str
    uploader: str = ""
    duration: float | None = None
    url: str | None = None
    words: list[str] = field(init=False)
    context: list[str] = field(init=False)
    lead: list[str] = field(init=False)
    days: set[date] = field(init=False)
    years: set[str] = field(init=False)
    numbers: set[int] = field(init=False)

    def __post_init__(self):
        self.title = " ".join(str(self.title or "").split())[:500]
        self.uploader = UPLOADER_SUFFIX.sub("", " ".join(str(self.uploader or "").split()))[:200]
        self.days, undated = _dates(self.title)
        self.words = _words(undated)  # a date's month and day are not key words
        self.context = _words(self.uploader)
        # The key words before the event, which name the DJs when they are not the event.
        self.lead = _key_words(_words(LEAD_END.split(undated, maxsplit=1)[0]))
        self.years = {w for w in self.words if YEAR.match(w)} | {str(d.year) for d in self.days}
        self.numbers = _numbers(self.words)
        length = self.duration
        number = isinstance(length, int | float) and not isinstance(length, bool)
        self.duration = float(length) if number and length > 0 else None

    def queries(self) -> list[str]:
        """Searches from most to least specific: pages with all the title's key words (and its
        year), pages with any of them or the uploader's (best first), then the uploader with
        the title's lead, which is usually the DJ."""
        title = _dates(self.title)[1]
        words = _search_words(title)
        uploader = _search_words(self.uploader)
        lead = _search_words(LEAD_END.split(title, maxsplit=1)[0]) or words
        either = list(dict.fromkeys(words + uploader))[:8]
        year = list(self.years) if len(self.years) == 1 and words else []
        queries = [
            " ".join(words[:6] + year),
            " OR ".join(either) if len(either) > 1 else "",
            " ".join(list(dict.fromkeys(uploader + lead))[:6]) if uploader else "",
        ]
        return [query for query in dict.fromkeys(queries) if query]


@dataclass
class Page:
    """A MixesDB page; its title reads "2013-09-27 - Kaytranada @ Boiler Room Montreal 002"."""

    pageid: int
    title: str
    wikitext: str
    parsed: dict = field(init=False)
    year: str | None = field(init=False)
    days: set[date] = field(init=False)
    names: list[list[str]] = field(init=False)
    event_words: list[str] = field(init=False)
    numbers: set[int] = field(init=False)
    words: set[str] = field(init=False)

    def __post_init__(self):
        self.parsed = parse_page(self.wikitext)
        prefix = PAGE_DATE.match(self.title)
        self.year = prefix.group(1) if prefix else None
        self.days, _ = _dates(self.title)
        rest = self.title[prefix.end() :] if prefix else self.title
        self.words = set(_words(rest))
        djs, _, event = rest.partition(" @ ") if " @ " in rest else rest.partition(" - ")
        self.names = []
        for name in NAME_SPLIT.split(re.sub(r"\([^)]*\)", " ", djs)):
            words = _words(name)
            if words[:1] == ["dj"] and len(words) > 1:
                words = words[1:]
            if words:
                self.names.append(words)
        self.event_words = _words(event)
        self.numbers = _numbers(_words(_dates(event)[1]))

    def uploads(self) -> set[str]:
        """The page's links, spelled as ``url_key`` spells them."""
        return {key for link in self.parsed["links"] if (key := url_key(link))}


def _possible(wanted: Wanted, page: Page) -> bool:
    """Whether nothing rules the page out: a DJ either side names that the other does not, or
    a year, day, number or length that disagrees.

    The uploader stands in for a DJ only when the title names none, so a DJ's channel that
    uploads a guest's set is not taken for the DJ.
    """
    if any(word not in page.words for word in wanted.lead):
        return False
    uploader_counts = all(word in page.event_words for word in wanted.lead)
    for name in page.names:
        if not (_has(wanted.words, name) or (uploader_counts and _has(wanted.context, name))):
            return False
    years = wanted.years
    if years and page.year not in years and not years & set(page.event_words):
        return False
    if _same_day(wanted.days, page.days) is False:
        return False
    if wanted.numbers and page.numbers and not wanted.numbers & page.numbers:
        return False
    lengths = page.parsed["lengths"]
    return not wanted.duration or not lengths or any(_near(wanted.duration, n) for n in lengths)


def _evidence(wanted: Wanted, page: Page) -> str:
    """Besides the title, what says the page is the upload: the same "date", the same
    "duration", or nothing."""
    if _same_day(wanted.days, page.days):
        return "date"
    return "duration" if wanted.duration and page.parsed["lengths"] else ""


def assess(wanted: Wanted, page: Page) -> float | None:
    """How surely the page is the upload by its title (0–1), or None when it is not.

    The page's DJs must be in the upload's title (or its uploader), and most of the page's
    event words ("Boiler Room Montreal") too. A DJ, year, day, number or length that
    disagrees rules the page out. Without a day or length that agrees only the same key
    words on both sides will do, and a radio show's episode needs the day: its length is
    every episode's.
    """
    event = _key_words(page.event_words)
    if not page.names or not event or not _possible(wanted, page):
        return None
    named = {word for name in page.names for word in name}
    seen = set(_key_words(wanted.words + wanted.context))
    asked = [w for w in _key_words(wanted.words) if w not in named]
    event_share = sum(1 for w in event if w in seen) / len(event)
    asked_share = sum(1 for w in asked if w in page.words) / len(asked) if asked else 1.0
    score = round(0.7 * event_share + 0.3 * asked_share, 2)
    evidence = _evidence(wanted, page)
    if page.parsed["radio_show"] and evidence != "date":
        return None
    if evidence:
        return score if event_share >= EVENT_SHARE and score >= MIN_SCORE else None
    return score if score >= STRONG_SCORE else None


def _same_show(one: Page, other: Page) -> bool:
    """Whether two pages may be episodes of one show: the same DJs, and the event words of
    one take in the other's ("Diynamic, Proton Radio" and "Hello Weekend (Diynamic, …)")."""
    if sorted(one.names) != sorted(other.names):
        return False
    mine, theirs = set(_key_words(one.event_words)), set(_key_words(other.event_words))
    return bool(mine and theirs) and (mine <= theirs or theirs <= mine)


def best_match(wanted: Wanted, pages: list[Page]) -> tuple[Page, float] | None:
    """The page that matches by title clearly better than any other one, or None. Another
    episode of the same show that nothing rules out is a rival whatever its score."""
    scored = sorted(
        ((score, page) for page in pages if (score := assess(wanted, page)) is not None),
        key=lambda pair: -pair[0],
    )
    if not scored or (len(scored) > 1 and scored[1][0] > scored[0][0] - MARGIN):
        return None  # nothing, or two pages alike, such as the same show in two years
    score, best = scored[0]
    if any(
        _same_show(best, page) and _possible(wanted, page) for page in pages if page is not best
    ):
        return None
    return best, score


def _only_part(wanted: Wanted, page: Page) -> bool:
    """Whether the upload is clearly shorter than the whole set the page lists."""
    lengths = page.parsed["lengths"]
    return bool(wanted.duration and lengths) and all(
        wanted.duration < PART_SHARE * length and not _near(wanted.duration, length)
        for length in lengths
    )


# Network


class Api:
    """MixesDB's MediaWiki API, at most MAX_REQUESTS requests per lookup."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.requests = 0

    @property
    def spent(self) -> bool:
        return self.requests >= MAX_REQUESTS

    async def search(self, query: str) -> list[Page]:
        return await self.pages({"generator": "search", "gsrsearch": query, "gsrlimit": RESULTS})

    async def linking(self, spelling: str) -> list[Page]:
        """Pages that link the upload. A YouTube ID is searched as text, which finds every
        spelling of the link; other links by their address, with either scheme."""
        if spelling.startswith("youtube:"):
            return await self.search(f'"{spelling.removeprefix("youtube:")}"')
        return await self.pages(
            {
                "generator": "exturlusage",
                "geuquery": spelling,
                "geunamespace": 0,
                "geulimit": RESULTS,
            }
        )

    async def pages(self, params: dict) -> list[Page]:
        """The pages a query generates, with their wikitext, in the search's order."""
        if self.spent:
            return []
        self.requests += 1
        query = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            **params,
        }
        try:
            response = await self.client.get(
                API, params=query, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            )
        except httpx.TimeoutException as exc:
            raise AppError("SOURCE_TIMEOUT", "MixesDB did not answer in time.", 504, True) from exc
        except httpx.HTTPError as exc:
            raise AppError("SOURCE_FAILED", "MixesDB could not be reached.", 502, True) from exc
        if response.status_code == 429:
            raise AppError("SOURCE_RATE_LIMITED", "MixesDB asked for fewer requests.", 502, True)
        if response.status_code != 200 or len(response.content) > MAX_REPLY_BYTES:
            raise AppError("SOURCE_FAILED", "MixesDB gave no usable answer.", 502, True)
        try:
            reply = response.json()
            if not isinstance(reply, dict) or "error" in reply:
                raise ValueError("A MediaWiki query reply is required.")
        except (ValueError, RecursionError) as exc:
            raise AppError("SOURCE_INVALID", "MixesDB gave no usable answer.", 502) from exc
        body = reply.get("query")
        entries = body.get("pages") if isinstance(body, dict) else None
        entries = entries if isinstance(entries, list) else []
        pages = []
        for entry in sorted((p for p in entries if isinstance(p, dict)), key=_index):
            try:
                content = entry["revisions"][0]["slots"]["main"]["content"]
                pages.append(Page(int(entry["pageid"]), _text(entry["title"]), _text(content)))
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # a missing page, one without wikitext, or broken text
        return pages


def _index(entry: dict) -> int:
    index = entry.get("index")
    return index if isinstance(index, int) else 0


def _text(value) -> str:
    """``value`` as text; a lone surrogate, which no page can hold, is a ValueError."""
    text = str(value)
    text.encode()
    return text


def _answer(page: Page, by: str, score: float) -> dict | None:
    parsed = page.parsed
    if not parsed["tracks"]:
        return None
    return {
        "source": "mixesdb",
        "page_title": page.title,
        "page_url": page_url(page.title),
        "text": tracklist_text(parsed["tracks"]),
        "complete": parsed["tracklist"] == "complete",
        "tracklist": parsed["tracklist"],
        "tracks": len(parsed["tracks"]),
        "duration_seconds": parsed["duration_seconds"],
        "players": parsed["players"],
        "match": {"by": by, "score": score},
    }


async def _linking(api: Api, key: str, spelling: str, pages: dict[int, Page]) -> list[Page]:
    """The pages that link the upload among those MixesDB gives for ``spelling``; all of
    them join ``pages`` for the title search."""
    candidates = await api.linking(spelling)
    pages.update((page.pageid, page) for page in candidates)
    return [page for page in candidates if key in page.uploads()]


def _linked_answer(wanted: Wanted, linked: list[Page]) -> dict | None:
    if len(linked) > 1:
        return None  # several pages link the upload; none can be told apart
    if _only_part(wanted, linked[0]):
        return None  # the upload is one part of the set, so most of the tracks are not in it
    return _answer(linked[0], "url", 1.0)  # None when the page lists no tracks


async def _find(api: Api, wanted: Wanted) -> dict | None:
    pages: dict[int, Page] = {}
    key, spellings = url_key(wanted.url), link_spellings(wanted.url)
    if key and (linked := await _linking(api, key, spellings[0], pages)):
        return _linked_answer(wanted, linked)
    for query in wanted.queries() if wanted.words else []:
        if api.spent:
            break
        for page in await api.search(query):
            pages.setdefault(page.pageid, page)
        if match := best_match(wanted, list(pages.values())):
            page, score = match
            evidence = _evidence(wanted, page)
            return _answer(page, f"title_{evidence}" if evidence else "title", score)
    for spelling in spellings[1:]:
        if key and (linked := await _linking(api, key, spelling, pages)):
            return _linked_answer(wanted, linked)
    return None


async def lookup(
    *,
    title: str,
    uploader: str | None = None,
    duration: float | None = None,
    url: str | None = None,
    client: httpx.AsyncClient | None = None,
    strict: bool = False,
) -> dict | None:
    """The MixesDB tracklist of the set at ``url`` (titled ``title``), or None.

    At most MAX_REQUESTS requests within DEADLINE seconds; no page, or a doubtful one, is
    None. So is an error or no answer in time, unless ``strict``: then that raises (an
    AppError, or TimeoutError), so callers can ask again later instead of keeping None.
    Returns ``{"source": "mixesdb", "page_title", "page_url", "text",
    "complete", "tracklist" ("complete", "incomplete" or "unknown"), "tracks",
    "duration_seconds", "players", "match": {"by", "score"}}``. ``match.by`` is "url" when the
    page links the upload (and the upload is not just one part of the set), "title_date"
    when the title and the day agree, "title_duration" when the title and length agree, and
    "title" when neither is known and both titles have the same key words. ``text`` reads as
    a listing through ``set_identification.listed_tracklist("mixesdb", text, page_url)``.
    """
    wanted = Wanted(title, uploader or "", duration, url)
    try:
        async with asyncio.timeout(DEADLINE):
            if client is not None:
                return await _find(Api(client), wanted)
            async with httpx.AsyncClient(follow_redirects=True) as own:
                return await _find(Api(own), wanted)
    except AppError as error:
        logger.debug("MixesDB lookup stopped: %s (%s)", error.message, error.code)
        if strict:
            raise
    except TimeoutError:
        logger.debug("MixesDB lookup stopped after %s seconds", DEADLINE)
        if strict:
            raise
    return None
