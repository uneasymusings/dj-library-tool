"""A set's tracklist from MixesDB, the wiki of DJ mixes, through its open MediaWiki API.

A page counts as the set only when it links the same upload, or when its title names the
same DJ and event and its length agrees with the upload's. A wrong tracklist is worse than
none, so a doubtful page, an outage or a slow answer gives no tracklist rather than an error.
"""

import asyncio
import html
import logging
import re
import unicodedata
from dataclasses import dataclass, field
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
RESULTS = 8
SLACK_SECONDS = 60.0
SLACK_SHARE = 0.02
EVENT_SHARE = 0.75  # of the page's event words the upload must name
MIN_SCORE = 0.6
STRONG_SCORE = 1.0  # either length unknown: every key word on both sides
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
LINK = re.compile(r"https?://[^\s|\[\]{}<>\"']+")

COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
REF = re.compile(r"<ref\b[^>/]*/>|<ref\b[^>]*>.*?</ref\s*>", re.IGNORECASE | re.DOTALL)
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
WIKI_LINK = re.compile(r"\[\[([^|\]]*)(?:\|([^\]]*))?\]\]")
WEB_LINK = re.compile(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]")
TAG = re.compile(r"<[^<>]{1,200}>")
HEADING = re.compile(r"^(=+)\s*(.*?)\s*=+$")
LIST_MARK = re.compile(r"^[#*]+(?![#*:;])\s*")
LIST_TAG = re.compile(r"</?list\s*>", re.IGNORECASE)
PLAYED_OVER = re.compile(r"^\+\s+")
CUE = re.compile(r"^\[\s*([\d?]{1,3}(?::[\d?]{1,2}){0,2})\s*\]\s*")
GAP = re.compile(r"^(?:\.{2,}|…|-+)$")
PART = re.compile(
    r"^(?:cd|disc|disk|part|pt\.?|side|chapter|hour|set)\s*[\w.]{0,4}\s*:?$", re.IGNORECASE
)
TABLE_CLOCK = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?![\d:])")
STANDARD_SHOW = re.compile(r"\{\{\s*StandardShow(\d+)(h|min)\s*\}\}", re.IGNORECASE)
PLAYER = re.compile(r"\{\{\s*Player\s*\|(.*?)\}\}", re.IGNORECASE | re.DOTALL)
STATUS = re.compile(
    r"\[\[\s*Category\s*:\s*Tracklist\s*:\s*(complete|incomplete|none)\s*(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)


# Pages


def parse_page(wikitext: str) -> dict:
    """What a page says about its mix: tracks in order (``(time or None, label)``), length,
    player links, every link, and "complete", "incomplete" or "unknown" for its tracklist."""
    text = COMMENT.sub("", wikitext[:MAX_PAGE_CHARS])
    sections = _sections(text)
    status = STATUS.search(text)
    players = [_link_end(link) for body in PLAYER.findall(text) for link in LINK.findall(body)]
    return {
        "tracks": _tracks(sections.get("tracklist", "")),
        "duration_seconds": _duration(sections.get("file details", "")),
        "players": list(dict.fromkeys(players)),
        "links": list(dict.fromkeys(_link_end(link) for link in LINK.findall(text))),
        "tracklist": status.group(1).lower() if status else "unknown",
    }


def tracklist_text(tracks: list[tuple[str | None, str]]) -> str:
    """Numbered "1. [00:45] Artist - Title (Mix)" lines, as ``parse_tracklist`` reads them.

    The numbers keep every line a track, also an untimed one above the first timed line,
    which an unnumbered list would read as a heading.
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
        heading = HEADING.match(line.strip())
        if heading and len(heading.group(1)) == 2:
            current = heading.group(2).casefold()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def _tracks(section: str) -> list[tuple[str | None, str]]:
    """List items ("# [00:45] …") and the lines of a <list> block, in order. Headings
    ("=== CD1 ===", ";Part 2", "Part 2"), notes ("#:") and gaps ("...") are not tracks;
    "+ " (played over the track before) is left out of the label."""
    tracks = []
    listing = False
    section = LIST_TAG.sub(lambda tag: "\n</list>\n" if "/" in tag[0] else "\n<list>\n", section)
    for line in section.splitlines():
        line = line.strip()
        if line in {"<list>", "</list>"}:
            listing = line == "<list>"
            continue
        mark = LIST_MARK.match(line)
        if not mark and not (listing and line and not line.startswith(("=", ";", ":"))):
            continue
        body = _plain(line[mark.end() :] if mark else line)
        time = None
        if cue := CUE.match(body):
            time, body = _cue_time(cue.group(1)), body[cue.end() :].strip()
        body = PLAYED_OVER.sub("", body, count=1)
        if body and not GAP.match(body) and not PART.match(body):
            tracks.append((time, body[:1000]))
    return tracks


def _plain(value: str) -> str:
    """A track line without wiki markup: "'''[[Moodymann|KDJ]]'''<ref>…</ref>" is "KDJ"."""
    value = REF.sub("", value)
    while TEMPLATE.search(value):
        value = TEMPLATE.sub("", value)
    value = WIKI_LINK.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), value)
    value = WEB_LINK.sub(r"\1", value)
    value = TAG.sub(" ", value.replace("'''", "").replace("''", ""))
    value = html.unescape(value)
    value = "".join(c for c in value if c.isspace() or unicodedata.category(c) != "Cc")
    return " ".join(value.split())


def _cue_time(value: str) -> str | None:
    """MixesDB writes "[00:45]", "[1:02:03]" or minutes alone ("[042]"); "?" is unknown."""
    if "?" in value:
        return None
    parts = [int(part) for part in value.split(":")]
    seconds = 0
    for part in parts if len(parts) > 1 else [parts[0], 0]:
        seconds = seconds * 60 + part
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes:02d}:{rest:02d}"


def _duration(section: str) -> float | None:
    """The first length in the "File details" table, else a standard show's slot."""
    for line in section.splitlines():
        line = line.strip()
        if line.startswith(("|", "!")) and "://" not in line and (m := TABLE_CLOCK.search(line)):
            hours, minutes, seconds = (int(part or 0) for part in m.groups())
            if total := hours * 3600 + minutes * 60 + seconds:
                return float(total)
    if show := STANDARD_SHOW.search(section):
        return float(int(show.group(1)) * (3600 if show.group(2).lower() == "h" else 60))
    return None


def _link_end(link: str) -> str:
    return link.rstrip(".,;:!)")


# Links


def url_key(url: str | None) -> str | None:
    """One spelling per upload: youtu.be, watch?v= and embed links are "youtube:<id>"; other
    links lose their scheme, "www."/"m.", query and trailing slash ("soundcloud.com/a/b")."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host:
        return None
    host = re.sub(r"^(?:www|m|mobile|music)\.", "", host)
    path = parts.path.rstrip("/")
    if host == "youtu.be":
        video = path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "youtube-nocookie.com"}:
        segments = path.lstrip("/").split("/")
        if segments[0] == "watch":
            video = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            video = segments[1] if len(segments) > 1 and segments[0] in YOUTUBE_PATHS else ""
    else:
        return f"{host}{path.lower()}" if path else None
    return f"youtube:{video}" if YOUTUBE_ID.match(video) else None


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
    return {int(w) for w in words if w.isdigit() and len(w) <= 4 and not YEAR.match(w)}


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

    def __post_init__(self):
        self.title = " ".join(str(self.title or "").split())[:500]
        self.uploader = UPLOADER_SUFFIX.sub("", " ".join(str(self.uploader or "").split()))[:200]
        self.words = _words(self.title)
        self.context = _words(self.uploader)
        length = self.duration
        number = isinstance(length, int | float) and not isinstance(length, bool)
        self.duration = float(length) if number and length > 0 else None

    def queries(self) -> list[str]:
        """Searches from most to least specific: pages with all the title's key words, pages
        with any of them or the uploader's (best first), then the uploader with the title's
        lead, which is usually the DJ."""
        words = _search_words(self.title)
        uploader = _search_words(self.uploader)
        lead = _search_words(LEAD_END.split(self.title, maxsplit=1)[0]) or words
        either = list(dict.fromkeys(words + uploader))[:8]
        queries = [
            " ".join(words[:6]),
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
    names: list[list[str]] = field(init=False)
    event_words: list[str] = field(init=False)
    words: set[str] = field(init=False)

    def __post_init__(self):
        self.parsed = parse_page(self.wikitext)
        date = PAGE_DATE.match(self.title)
        self.year = date.group(1) if date else None
        rest = self.title[date.end() :] if date else self.title
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

    def uploads(self) -> set[str]:
        """The page's links, spelled as ``url_key`` spells them."""
        return {key for link in self.parsed["links"] if (key := url_key(link))}


def assess(wanted: Wanted, page: Page) -> float | None:
    """How surely the page is the upload by its title (0–1), or None when it is not.

    Every DJ the page names must be in the upload's title or uploader, and most of the
    page's event words ("Boiler Room Montreal") too. A year or number that disagrees, or a
    length that does not, rules the page out. Without both lengths only the same key words
    on both sides will do.
    """
    event = _key_words(page.event_words)
    if not page.names or not event:
        return None
    if not all(_has(wanted.words, name) or _has(wanted.context, name) for name in page.names):
        return None
    years = {w for w in wanted.words if YEAR.match(w)}
    if years and page.year not in years and not years & set(page.event_words):
        return None
    numbers, page_numbers = _numbers(wanted.words), _numbers(page.event_words)
    if numbers and page_numbers and not numbers & page_numbers:
        return None
    named = {word for name in page.names for word in name}
    seen = set(_key_words(wanted.words + wanted.context))
    asked = [w for w in _key_words(wanted.words) if w not in named]
    event_share = sum(1 for w in event if w in seen) / len(event)
    asked_share = sum(1 for w in asked if w in page.words) / len(asked) if asked else 1.0
    score = round(0.7 * event_share + 0.3 * asked_share, 2)
    length = page.parsed["duration_seconds"]
    if wanted.duration and length:
        if abs(wanted.duration - length) > max(SLACK_SECONDS, SLACK_SHARE * length):
            return None
        return score if event_share >= EVENT_SHARE and score >= MIN_SCORE else None
    return score if score >= STRONG_SCORE else None


def best_match(wanted: Wanted, pages: list[Page]) -> tuple[Page, float] | None:
    """The page that matches by title clearly better than any other one, or None."""
    scored = sorted(
        (
            (score, page)
            for page in pages
            if page.parsed["tracks"] and (score := assess(wanted, page)) is not None
        ),
        key=lambda pair: -pair[0],
    )
    if not scored or (len(scored) > 1 and scored[1][0] > scored[0][0] - MARGIN):
        return None  # nothing, or two pages alike, such as the same show in two years
    return scored[0][1], scored[0][0]


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

    async def linking(self, key: str) -> list[Page]:
        """Pages that link the upload. A YouTube ID is searched as text, which finds every
        spelling of the link; other links by their address, with either scheme."""
        if key.startswith("youtube:"):
            return await self.search(f'"{key.removeprefix("youtube:")}"')
        return await self.pages(
            {"generator": "exturlusage", "geuquery": key, "geunamespace": 0, "geulimit": RESULTS}
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
        except ValueError as exc:
            raise AppError("SOURCE_INVALID", "MixesDB gave no usable answer.", 502) from exc
        body = reply.get("query")
        entries = body.get("pages") if isinstance(body, dict) else None
        pages = []
        for entry in sorted(
            (p for p in entries or [] if isinstance(p, dict)), key=lambda p: p.get("index", 0)
        ):
            try:
                content = entry["revisions"][0]["slots"]["main"]["content"]
                pages.append(Page(int(entry["pageid"]), str(entry["title"]), str(content)))
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # a missing page, or one without wikitext
        return pages


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


async def _find(api: Api, wanted: Wanted) -> dict | None:
    pages: dict[int, Page] = {}
    if key := url_key(wanted.url):
        candidates = await api.linking(key)
        linked = [page for page in candidates if key in page.uploads()]
        if len(linked) == 1:
            return _answer(linked[0], "url", 1.0)  # None when the page lists no tracks
        if linked:
            return None  # several pages link the upload; none can be told apart
        pages.update((page.pageid, page) for page in candidates)
    for query in wanted.queries() if wanted.words else []:
        if api.spent:
            break
        for page in await api.search(query):
            pages.setdefault(page.pageid, page)
        if match := best_match(wanted, list(pages.values())):
            page, score = match
            timed = wanted.duration and page.parsed["duration_seconds"]
            return _answer(page, "title_duration" if timed else "title", score)
    return None


async def lookup(
    *,
    title: str,
    uploader: str | None = None,
    duration: float | None = None,
    url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """The MixesDB tracklist of the set at ``url`` (titled ``title``), or None.

    At most MAX_REQUESTS requests within DEADLINE seconds; no answer, an error or a doubtful
    page is None. Returns ``{"source": "mixesdb", "page_title", "page_url", "text",
    "complete", "tracklist" ("complete", "incomplete" or "unknown"), "tracks",
    "duration_seconds", "players", "match": {"by", "score"}}``. ``match.by`` is "url" when the
    page links the upload, "title_duration" when title and length agree, and "title" when
    either length is unknown and both titles have the same key words. ``text`` reads as a
    listing through ``set_identification.listed_tracklist("mixesdb", text, page_url)``.
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
    except TimeoutError:
        logger.debug("MixesDB lookup stopped after %s seconds", DEADLINE)
    return None
