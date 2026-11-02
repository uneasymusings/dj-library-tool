"""Preview a bounded playlist before checking it against the local music catalog."""

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime

from pydantic import Field, field_validator

from djlib.application.requests import create_request, refresh_request
from djlib.application.tracklists import HEADING, parse_tracklist
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import (
    RequestContract,
    RequestCreate,
    RequestItem,
    RequestRefresh,
    SetSource,
    clean_text,
)


class PlaylistImport(RequestContract):
    name: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10_000_000)
    latest: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("name")
    @classmethod
    def playlist_name(cls, value):
        return clean_text(value)


def _header(value):
    return re.sub(r"[^a-z0-9]", "", value.lower().lstrip("\ufeff"))


def _date(value):
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        # A date without an offset has no reliable place beside a timestamp with one.
        if date.tzinfo is None:
            return None
        return date.astimezone(UTC)
    except ValueError:
        return None


def preview_playlist(body: PlaylistImport) -> dict:
    text = body.text.lstrip("\ufeff").strip()
    if text.startswith(("https://open.spotify.com/", "spotify:playlist:")) and "\n" not in text:
        raise AppError(
            "PLAYLIST_EXPORT_REQUIRED",
            "Choose a Spotify playlist CSV with Track Name and Artist Name(s), or paste "
            "an Artist - Title tracklist. A playlist link alone does not include its songs.",
        )
    warnings, rows, format_name = [], [], "tracklist"
    # Only recognize a spreadsheet by its column names; a comma in a song is not CSV.
    for delimiter in (",", "\t", ";"):
        try:
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
            headers = {_header(key): key for key in reader.fieldnames or []}
            title = next((headers[k] for k in ("trackname", "title", "name") if k in headers), None)
            artist = next(
                (
                    headers[k]
                    for k in ("artistnames", "artistname", "artists", "artist")
                    if k in headers
                ),
                None,
            )
            if not title or not artist:
                continue
            if len(headers) != len(reader.fieldnames):
                raise AppError("INPUT_INVALID", "CSV column names must be unique.")
            added = next((headers[k] for k in ("addedat", "dateadded") if k in headers), None)
            uri = next(
                (headers[k] for k in ("trackuri", "trackurl", "spotifyuri") if k in headers), None
            )
            version = next((headers[k] for k in ("version", "mix") if k in headers), None)
            format_name = (
                "spotify_csv" if "trackname" in headers and "artistnames" in headers else "csv"
            )
            for number, row in enumerate(reader, 2):
                if len(rows) >= 10_000:
                    raise AppError(
                        "PLAYLIST_TOO_LARGE", "Import at most 10,000 playlist rows at once."
                    )
                if None in row or any(value is None for value in row.values()):
                    raise AppError(
                        "INPUT_INVALID", f"CSV row {number} has the wrong number of columns."
                    )
                link = (row.get(uri) or "").strip() if uri else ""
                match = re.fullmatch(
                    r"(?:spotify:track:|https://open\.spotify\.com/track/)([A-Za-z0-9]{22})(?:\?[^\s]*)?",
                    link,
                )
                try:
                    item = RequestItem(
                        artist=(row[artist] or "").strip().replace(";", ", "),
                        title=(row[title] or "").strip(),
                        version=(row.get(version) or "").strip() if version else "",
                        source_url=f"https://open.spotify.com/track/{match[1]}" if match else None,
                    )
                except ValueError as exc:
                    raise AppError(
                        "INPUT_INVALID", f"CSV row {number} needs a valid artist and title."
                    ) from exc
                stamp = (row.get(added) or "").strip() if added else ""
                rows.append(
                    {
                        "position": number - 1,
                        "item": item,
                        "date": _date(stamp),
                        "added_at": stamp or None,
                    }
                )
            break
        except csv.Error as exc:
            raise AppError(
                "INPUT_INVALID", "The CSV is incomplete or has an unclosed quoted field."
            ) from exc
    else:
        items, skipped = parse_tracklist(text)
        warnings = [
            f"Line {n} left out: {reason}." for n, _, reason in skipped if reason != HEADING
        ]
        rows = [
            {"position": n, "item": item, "date": None, "added_at": None}
            for n, item in enumerate(items, 1)
        ]
    if not rows:
        raise AppError(
            "INPUT_INVALID", "No songs found. Use Artist - Title lines or a playlist CSV."
        )
    total = len(rows)
    if body.latest is not None:
        if any(row["date"] is None for row in rows):
            raise AppError(
                "PLAYLIST_DATES_REQUIRED",
                "Latest songs needs an Added At column with an ISO timestamp and timezone "
                "for every row (for example 2026-10-30T18:00:00Z). Choose playlist order "
                "to import without dates. Release dates are not dates added.",
            )
        rows = sorted(rows, key=lambda row: row["date"], reverse=True)[: body.latest]
    if len(rows) > 1000:
        raise AppError(
            "PLAYLIST_SELECTION_REQUIRED",
            f"This playlist has {len(rows):,} songs. Choose the latest 1,000 or fewer with "
            "Added At timestamps, or import a smaller file. No songs were dropped.",
        )
    return {
        "name": body.name.strip(),
        "format": format_name,
        "total_rows": total,
        "selected_rows": len(rows),
        "selection": "latest_by_date_added" if body.latest is not None else "playlist_order",
        "order": "newest_first" if body.latest is not None else "as_supplied",
        "warnings": warnings,
        "tracks": [
            {
                "source_position": r["position"],
                "added_at": r["added_at"],
                **r["item"].model_dump(mode="json"),
            }
            for r in rows
        ],
    }


def import_playlist(application, body: PlaylistImport) -> dict:
    preview = preview_playlist(body)
    items = []
    for row in preview["tracks"]:
        item = {k: v for k, v in row.items() if k not in {"source_position", "added_at"}}
        item["notes"] = f"Playlist position {row['source_position']}" + (
            f"; added {row['added_at']}" if row["added_at"] else ""
        )
        items.append(RequestItem.model_validate(item))
    digest = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
    request = RequestCreate(
        name=body.name,
        items=items,
        idempotency_key=f"playlist-import:{digest}",
        source=SetSource(
            provider="Spotify" if preview["format"] == "spotify_csv" else "Playlist",
            title=body.name,
        ),
    )
    result = create_request(application, request)
    if result.get("reused"):
        result = refresh_request(
            application, result["request_id"], RequestRefresh(revision=result["revision"])
        )
    return {"request": result, "selection": {k: v for k, v in preview.items() if k != "tracks"}}
