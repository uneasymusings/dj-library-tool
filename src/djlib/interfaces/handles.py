"""Short handles for saved crates and request lists, so nobody has to paste a long ID.

``resolve(local, "collection", value)`` accepts a full ID, ``last`` (the newest), an exact
name (any case) or a unique ID prefix of at least six characters, with or without the
``collection_`` / ``request_`` prefix. It only reads the saved-list endpoints.
"""

import re

from djlib.domain.errors import AppError

KINDS = {
    # kind: (list endpoint, result key, ID field, what a DJ calls it, where to look)
    "collection": ("/collections", "collections", "collection_id", "crate", "djlib crates"),
    "request": ("/requests", "requests", "request_id", "request list", "djlib requests list"),
}
FULL_ID = re.compile(r"[0-9a-f]{32}")
HEX = re.compile(r"[0-9a-f]+")
MIN_PREFIX = 6
PAGE = 100
MAX_PAGES = 20  # 2,000 rows; a handle that matches more than that is not a handle
MAX_QUERY = 500  # the list endpoints refuse longer queries
SHOWN = 5


def resolve(local, kind: str, value: str) -> str:
    """The full ID that ``value`` names; NOT_FOUND or AMBIGUOUS when it names none or several."""
    if kind not in KINDS:
        raise ValueError(f"Unknown handle kind: {kind}")
    path, key, field, noun, where = KINDS[kind]
    prefix = f"{kind}_"
    text = (value or "").strip()
    lowered = text.lower()
    if lowered.startswith(prefix) and FULL_ID.fullmatch(lowered[len(prefix) :]):
        return lowered
    if not text:
        raise AppError("INPUT_INVALID", f"Name a {noun}: its name, an ID, or last.")
    if lowered == "last":
        newest = _rows(local, path, key, "", limit=1, pages=1)
        if not newest:
            raise AppError("NOT_FOUND", f"There is no {noun} yet; nothing to pick as last.", 404)
        return newest[0][field]
    if len(text) <= MAX_QUERY:
        named = [
            row
            for row in _rows(local, path, key, text)
            if (row.get("name") or "").strip().casefold() == text.casefold()
        ]
        if len(named) == 1:
            return named[0][field]
        if named:
            raise _ambiguous(text, named, field, noun)
    digits = lowered[len(prefix) :] if lowered.startswith(prefix) else lowered
    if len(digits) >= MIN_PREFIX and HEX.fullmatch(digits):
        start = prefix + digits
        found = [row for row in _rows(local, path, key, digits) if row[field].startswith(start)]
        if len(found) == 1:
            return found[0][field]
        if found:
            raise _ambiguous(text, found, field, noun)
    raise AppError(
        "NOT_FOUND",
        f"No {noun} matches “{text}”. List them with: {where}. Use a name, "
        f"the first {MIN_PREFIX} characters of an ID, or last.",
        404,
    )


def _rows(local, path: str, key: str, query: str, *, limit: int = PAGE, pages: int = MAX_PAGES):
    rows, after = [], None
    for _ in range(pages):
        params = {"query": query, "limit": limit}
        if after is not None:
            params["after"] = after
        result = local.request("GET", path, params=params)["result"]
        rows += result.get(key) or []
        after = result.get("next_cursor")
        if after is None:
            break
    return rows


def _ambiguous(text: str, rows: list[dict], field: str, noun: str) -> AppError:
    shown = [f"{row.get('name') or 'untitled'} ({row[field]})" for row in rows[:SHOWN]]
    more = f" and {len(rows) - SHOWN} more" if len(rows) > SHOWN else ""
    return AppError(
        "AMBIGUOUS",
        f"“{text}” matches {len(rows)} {noun}s: {'; '.join(shown)}{more}. "
        "Use more of the ID to pick one.",
    )
