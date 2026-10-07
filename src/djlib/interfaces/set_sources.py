"""`djlib set` helpers: a tracklist from a set's URL, comment hints for IDs, fetch planning.

These run in the CLI process against the coordinator; network work happens in the
coordinator's source endpoints. Nothing here identifies audio: comments and search results
are evidence, and only confident search matches are offered for download.
"""

import hashlib
import json
import re
from urllib.parse import urlsplit

from djlib.domain.errors import AppError

BROWSER_ONLY = ("1001tracklists.com",)
SET_HOSTS = ("youtube.com", "youtu.be", "soundcloud.com")
COMMENTS = 1000
TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")


def check_set_url(url: str) -> None:
    name = host(url)
    if any(name == blocked or name.endswith("." + blocked) for blocked in BROWSER_ONLY):
        raise AppError(
            "SOURCE_BROWSER_ONLY",
            "1001Tracklists only shows tracklists to people in a browser. Copy the tracklist "
            "from the page into a text file and run djlib set FILE, or ask your assistant to "
            "read the page in your browser.",
        )
    if not any(name == known or name.endswith("." + known) for known in SET_HOSTS):
        raise AppError("SOURCE_UNSUPPORTED", "Use a YouTube or SoundCloud set, or a text file.")


def seconds(timestamp: str | None) -> float | None:
    match = TIME.match((timestamp or "").strip())
    if not match:
        return None
    hours, minutes, secs = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + secs


def tracklist_from_url(local, url: str) -> tuple[str, dict]:
    """The set's tracklist text (description or chapters) plus what was read from the page."""
    from djlib.application.source_matching import tracklist_from_source

    check_set_url(url)
    source = local.request("POST", "/sources/inspect", data={"url": url, "comments": COMMENTS})[
        "result"
    ]
    text = tracklist_from_source(source.get("description") or "", source.get("chapters") or [])
    return text, source


def id_hints(items: list[dict], comments: list[dict]) -> list[dict]:
    """For each unknown ID with a time, what listeners named around that point of the set."""
    if not comments:
        return []
    from djlib.application.source_matching import comment_hints

    hints = []
    for item in items:
        source = item.get("input") or {}
        if source.get("kind") != "unknown" or not comments:
            continue
        at = seconds(source.get("timestamp"))
        found = comment_hints(comments, at)[:3] if at is not None else []
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
        if best and best.get("confident"):
            chosen.append(
                {**song, "source": pick(best), "alternates": alternates(candidates, best)}
            )
        else:
            undecided.append(
                {
                    **song,
                    "reason": "no confident match" if candidates else "nothing found",
                    "options": [pick(candidate) for candidate in candidates[:3]],
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
