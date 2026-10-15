"""`djlib set` helpers: a tracklist from a set's URL, comment hints for IDs, fetch planning.

These run in the CLI process against the coordinator; network work happens in the
coordinator's source endpoints. Nothing here listens to a set (`djlib identify` does):
comments and search results are evidence, and only confident search matches are offered
for download.
"""

import contextlib
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from djlib.domain.errors import AppError

BROWSER_ONLY = ("1001tracklists.com", "1001.tl")
SET_HOSTS = ("youtube.com", "youtu.be", "soundcloud.com")
COMMENTS = 1000
TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")


def provider_of(url: str | None) -> str | None:
    """ "YouTube" or "SoundCloud" for an https link to a set there; None for anything else."""
    try:
        if not url or urlsplit(url).scheme != "https":
            return None
        name = host(url)
    except ValueError:
        return None
    if name in {"youtube.com", "youtu.be"}:
        return "YouTube"
    return "SoundCloud" if name == "soundcloud.com" else None


def browser_only(url: str, command: str = "set") -> None:
    """1001Tracklists: SOURCE_BROWSER_ONLY, with the ways to hand djlib its tracklist."""
    from djlib.sources.web import browser_only_details

    name = host(url)
    if any(name == blocked or name.endswith("." + blocked) for blocked in BROWSER_ONLY):
        raise AppError(
            "SOURCE_BROWSER_ONLY",
            "1001Tracklists only shows tracklists to people in a browser; djlib can't read it. "
            "An assistant that can browse can copy it for you.",
            details=browser_only_details(command),
        )


def check_set_url(url: str) -> None:
    from djlib.sources.web import mixcloud_details, mixcloud_message

    browser_only(url)
    name = host(url)
    if name == "mixcloud.com" or name.endswith(".mixcloud.com"):
        raise AppError("SOURCE_UNSUPPORTED", mixcloud_message(), details=mixcloud_details())
    if not any(name == known or name.endswith("." + known) for known in SET_HOSTS):
        raise AppError("SOURCE_UNSUPPORTED", "Use a YouTube or SoundCloud set, or a text file.")


def seconds(timestamp: str | None) -> float | None:
    match = TIME.match((timestamp or "").strip())
    if not match:
        return None
    hours, minutes, secs = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + secs


def tracklist_from_url(local, url: str) -> tuple[str, dict]:
    """The set's tracklist text (description or chapters, else MixesDB's) plus what was read
    from the page."""
    check_set_url(url)
    source = local.request(
        "POST", "/sources/inspect", data={"url": url, "comments": COMMENTS, "mixesdb": True}
    )["result"]
    text = page_tracklist(source)
    if not text and (found := from_mixesdb(source)):
        text = found["text"]
    return text, source


def saved_page_note(page: dict, url: str) -> str:
    """What `djlib set` says when the page of a set it heard before couldn't be read."""
    site = provider_of(page.get("url") or url) or "The site"
    when = ""
    with contextlib.suppress(TypeError, ValueError):
        read = datetime.fromisoformat(str(page.get("read_at"))).astimezone()
        when = f" on {read.day} {read:%b}"
    return f"Couldn't reach {site} just now; this is what djlib read and heard{when}."


def page_tracklist(page: dict) -> str:
    """The tracklist a set's page publishes (its chapters, else its description), or "" when
    no line of it reads as a track: YouTube names some sets' chapters itself, such as
    "Opening performance"."""
    return page_listing(page)[0]


def page_listing(page: dict) -> tuple[str, str | None]:
    """``page_tracklist`` and where it is: "chapters" or "description" (None without one)."""
    from djlib.application.source_matching import tracklist_from_source
    from djlib.application.tracklists import parse_tracklist

    description, chapters = page.get("description") or "", page.get("chapters") or []
    context = (str(page.get("uploader") or ""), str(page.get("title") or ""))
    for text in (
        tracklist_from_source(description, chapters, *context),
        tracklist_from_source(description, [], *context),
    ):
        if text and parse_tracklist(text, page.get("url"))[0]:
            chaptered = bool(chapters) and text == tracklist_from_source("", chapters, *context)
            return text, "chapters" if chaptered else "description"
    return "", None


def listed_in(page: dict) -> str:
    """Where `djlib set` found a set's published tracklist (``tracklist_from_url``):
    "chapters", "description" or "mixesdb"."""
    text, origin = page_listing(page)
    if not text and from_mixesdb(page) is not None:
        return "mixesdb"
    return origin or "description"


def from_mixesdb(page: dict) -> dict | None:
    """The MixesDB lookup a set's tracklist comes from: the coordinator looks MixesDB up only
    when the description and chapters list no tracklist."""
    found = page.get("mixesdb")
    return found if isinstance(found, dict) and isinstance(found.get("text"), str) else None


def id_hints(items: list[dict], comments: list[dict]) -> list[dict]:
    """For each unknown ID with a time, what listeners named around that point of the set,
    leaving out the songs either side of the ID however a listener wrote them."""
    if not comments:
        return []
    from djlib.application.set_identification import names_neighbour
    from djlib.application.source_matching import comment_hints

    hints = []
    for index, item in enumerate(items):
        source = item.get("input") or {}
        if source.get("kind") != "unknown" or not comments:
            continue
        at = seconds(source.get("timestamp"))
        beside = [
            (other.get("artist") or "", other.get("title") or "")
            for near in items[max(index - 1, 0) : index] + items[index + 1 : index + 2]
            if (other := near.get("input") or {}).get("kind") == "named"
        ]
        found = [] if at is None else comment_hints(comments, at)
        found = [hint for hint in found if not names_neighbour(hint, beside)][:3]
        if found:
            hints.append(
                {
                    "position": item.get("position"),
                    "item_id": item.get("item_id"),
                    "timestamp": source.get("timestamp"),
                    "hints": found,
                }
            )
    return hints


def comment_list(comments: list[dict]) -> list[dict]:
    """Everything listeners named across the comments, in set order, for structured output."""
    if not comments:
        return []
    from djlib.application.source_matching import comment_hints

    hints = comment_hints(comments, None)
    named = [
        {
            "label": hint["label"],
            "artist": hint["artist"],
            "title": hint["title"],
            "at": None if hint["at_seconds"] is None else clock(hint["at_seconds"]),
            "mentions": hint["mentions"],
            "likes": hint["likes"],
            "evidence": hint["evidence"][:1],
        }
        for hint in hints
    ]
    return sorted(named, key=lambda hint: (hint["at"] is None, seconds(hint["at"]) or 0))


def clock(value: float) -> str:
    total = int(value)
    hours, rest = divmod(total, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def named_in_comments(comments: list[dict]) -> str:
    """“ Listeners named 9 tracks in the comments, e.g. …” or an empty string."""
    if not comments:
        return ""
    from djlib.application.source_matching import comment_hints

    hints = comment_hints(comments, None)
    if not hints:
        return ""
    # Credited mentions first: they are the most useful pointers.
    examples = sorted(hints, key=lambda hint: not hint["artist"])[:4]
    count = f"{len(hints)} track{'s' if len(hints) != 1 else ''}"
    return (
        f" Listeners named {count} in the comments, e.g. "
        + "; ".join(hint["label"] for hint in examples)
        + "."
    )


def wanted(item: dict) -> dict:
    source = item.get("input") or {}
    return {key: source.get(key) or "" for key in ("artist", "title", "version")}


def label(item: dict) -> str:
    song = wanted(item)
    text = f"{song['artist']} - {song['title']}"
    return f"{text} ({song['version']})" if song["version"] else text


SEARCHES_AT_ONCE = 4


def plan_fetch(local, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Missing named songs → (confident downloads, songs that need a person's pick).

    Songs are searched a few at a time; results keep the set's order. Each download carries
    up to three other uploads of the same recording to fall back on.
    """
    from concurrent.futures import ThreadPoolExecutor

    from djlib.application.source_matching import alternates

    missing = [
        item
        for item in items
        if item.get("state") == "missing" and (item.get("input") or {}).get("kind") == "named"
    ]

    def search(item):
        try:
            return local.request("POST", "/sources/search", data=wanted(item))["result"]
        except AppError as error:
            return error

    with ThreadPoolExecutor(max_workers=SEARCHES_AT_ONCE) as pool:
        answers = list(pool.map(search, missing))
    chosen, undecided = [], []
    for item, found in zip(missing, answers, strict=True):
        song = {**wanted(item), "label": label(item), "position": item.get("position")}
        if isinstance(found, AppError):
            undecided.append({**song, "reason": found.code, "options": []})
            continue
        candidates = found.get("candidates") or []
        best = candidates[0] if candidates else None
        # Uploads that no longer play (removed, region-locked, DRM), so a person can see why
        # e.g. the artist's own upload wasn't used.
        skipped = {"skipped": found["unavailable"]} if found.get("unavailable") else {}
        if best and best.get("confident"):
            chosen.append(
                {
                    **song,
                    "source": pick(best),
                    "alternates": alternates(candidates, best),
                    **skipped,
                }
            )
        else:
            undecided.append(
                {
                    **song,
                    "reason": "no confident match" if candidates else "nothing found",
                    "options": [pick(candidate) for candidate in candidates[:3]],
                    **skipped,
                }
            )
    return chosen, undecided


def pick(candidate: dict) -> dict:
    keys = ("provider", "url", "title", "uploader", "duration", "score", "reasons", "available")
    return {key: candidate.get(key) for key in keys}


def download_body(name: str, request_id: str, chosen: list[dict]) -> dict:
    tracks = [
        {
            "url": entry["source"]["url"],
            **{key: entry[key] for key in ("artist", "title", "version")},
            **({"alternates": entry["alternates"]} if entry.get("alternates") else {}),
        }
        for entry in chosen
    ]
    fingerprint = json.dumps([request_id, tracks], sort_keys=True)
    return {
        "name": f"{name} — downloads"[:300],
        "idempotency_key": f"set-fetch:{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}",
        "tracks": tracks,
    }


def worth_retrying(local, job: dict) -> bool:
    """Whether any failed download may work on a second try (a refused stream, a timeout);
    uploads that are gone stay gone."""
    if not (job.get("counts") or {}).get("failed"):
        return False
    failed = local.request(
        "GET", f"/jobs/{job['job_id']}/items", params={"state": "failed", "limit": 100}
    )["result"]["items"]
    return any(((item.get("result") or {}).get("error") or {}).get("retryable") for item in failed)


# -- links: djlib only prints these; it never requests them ---------------------------------------

# "Song [ED BANGER]": the label a tracklist names, which a store search reads as words.
RECORD_LABEL = re.compile(r"\s*\[[^\[\]]*\]\s*$")
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
SOUNDCLOUD_HOSTS = frozenset({"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"})
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A SoundCloud set's path as djlib writes it, nothing that could end the link early.
SOUNDCLOUD_PATH = re.compile(r"^(?:/[A-Za-z0-9_.~%-]+)+$")


def shop_links(artist: str | None, title: str | None, version: str | None = "") -> dict:
    """Beatport, Bandcamp and Traxsource searches for a song: links for the DJ to open."""
    from urllib.parse import quote_plus

    from djlib.domain.contracts import plain_labels, split_featured

    title, version = plain_labels(
        RECORD_LABEL.sub("", title or ""), RECORD_LABEL.sub("", version or "")
    )
    words = (split_featured(artist or "")[0], split_featured(title)[0], version)
    # As the stores' own search forms send it: "+" between words, the rest percent-encoded.
    query = quote_plus(" ".join(" ".join(filter(None, words)).split()), safe="")
    return {
        "beatport": f"https://www.beatport.com/search?q={query}",
        "bandcamp": f"https://bandcamp.com/search?q={query}&item_type=t",
        "traxsource": f"https://www.traxsource.com/search?term={query}",
    }


def listen_url(source_url: str | None, seconds) -> str | None:
    """The set's link at that moment: YouTube's ``t=`` or SoundCloud's ``#t=``; None for any
    other site, a plain-http link or no time."""
    import math
    from urllib.parse import parse_qs

    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    try:
        parts = urlsplit(str(source_url or "").strip())
        name = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    at = round(seconds)
    if name == "youtu.be" or name in YOUTUBE_HOSTS:
        segments = [part for part in parts.path.split("/") if part]
        if name == "youtu.be":
            video = segments[0] if segments else ""
        else:
            video = (parse_qs(parts.query).get("v") or [""])[0]
            if not video and len(segments) == 2 and segments[0] in {"live", "embed", "v"}:
                video = segments[1]
        return f"https://www.youtube.com/watch?v={video}&t={at}s" if VIDEO_ID.match(video) else None
    if name in SOUNDCLOUD_HOSTS:
        path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
        if not SOUNDCLOUD_PATH.match(path):
            return None
        return f"https://soundcloud.com{path}#t={clock(at)}"
    return None
