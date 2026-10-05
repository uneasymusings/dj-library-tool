"""Turn a pasted tracklist into request items without guessing unknown identities.

Accepted line shapes, one track per line::

    Artist - Title (Extended Mix)
    03. Artist – Title [Label]
    [47:12] Artist - Title
    ID - ID   /   ??? - ???   (kept as an unknown with its timestamp or source)

Numbering, bullets and timestamps are stripped. Lines without an "Artist - Title" separator
are reported back instead of being turned into a guessed request.
"""

import re

from djlib.domain.request_contracts import RequestItem

SEPARATOR = re.compile(r"\s+[-–—]\s+")
NUMBERING = re.compile(r"^\s*(?:[-*•·]\s*|\d{1,3}\s*[.)]\s*|#\d{1,3}\s+)")
TIMESTAMP = re.compile(r"^\s*[\[(]?\s*(\d{1,2}(?::\d{2}){1,2})\s*[\])]?\s*[-–—]?\s*")
TRAILING_LABEL = re.compile(r"\s*\[([^\]]*)\]\s*$")
VERSION_WORDS = re.compile(
    r"\b(?:mix|edit|remix|dub|version|vip|rework|bootleg|instrumental|remaster(?:ed)?|live)\b",
    re.IGNORECASE,
)
UNKNOWN = re.compile(r"^(?:id|\?+|unknown|unreleased id|tba)$", re.IGNORECASE)


HEADING = "looks like a heading"
LEADING_NUMBER = re.compile(r"^\s*\d{1,3}(?:\s*[-.)_]\s*|\s+)(?=\S)")


def file_name_labels(stem: str) -> tuple[str, str]:
    """("Artist", "Title") from a file name like "03 - Artist - Title (Mix)", else ("", "")."""
    stem = LEADING_NUMBER.sub("", stem.replace("_", " ").strip(), count=1)
    parts = SEPARATOR.split(stem, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return "", ""
    if any(ord(c) < 32 for c in stem):
        return "", ""
    return parts[0].strip()[:500], parts[1].strip()[:1000]


def parse_tracklist(text: str, source_url: str | None = None) -> tuple[list[RequestItem], list]:
    """Return request items plus ``(line_number, text, reason)`` for lines left out.

    When most lines are numbered or timestamped, unmarked lines above the first numbered
    one are headings (a set name, a date) rather than tracks.
    """
    parsed = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        stripped = NUMBERING.sub("", line, count=1)
        marked = stripped != line
        timestamp = None
        if match := TIMESTAMP.match(stripped):
            timestamp, stripped, marked = match.group(1), stripped[match.end() :], True
        parsed.append(
            (number, raw.strip(), NUMBERING.sub("", stripped, count=1).strip(), timestamp, marked)
        )
    marked_lines = sum(1 for *_, marked in parsed if marked)
    numbered_list = marked_lines >= 2 and marked_lines * 2 >= len(parsed)

    items: list[RequestItem] = []
    skipped: list[tuple[int, str, str]] = []
    started = False
    for number, raw, line, timestamp, marked in parsed:
        started = started or marked
        if numbered_list and not started:
            skipped.append((number, raw, HEADING))
            continue
        parts = SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            if UNKNOWN.match(line):
                parts = [line, line]
            else:
                skipped.append((number, raw, "no “Artist - Title” separator"))
                continue
        artist = parts[0].strip()
        title = parts[1].strip()
        label = TRAILING_LABEL.search(title)
        # "[LABEL]" is a record label in most tracklists; "[Extended Mix]" is a version.
        if label and not VERSION_WORDS.search(label.group(1)):
            title = title[: label.start()].strip() or title
        if UNKNOWN.match(artist) or UNKNOWN.match(title):
            if not (timestamp or source_url):
                skipped.append((number, raw, "unknown ID needs a timestamp or --source"))
                continue
            items.append(
                RequestItem(
                    kind="unknown",
                    label=line[:1000],
                    timestamp=timestamp,
                    source_url=source_url,
                )
            )
            continue
        try:
            items.append(RequestItem(artist=artist[:500], title=title[:1000]))
        except ValueError:
            skipped.append((number, raw, "unreadable characters"))
    return items, skipped
