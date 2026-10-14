"""Persistent song requests with conservative catalog reuse and explicit unresolved evidence.

No provider calls, acquisition jobs, native app changes, or device writes happen here.
Saved matches are observations at refresh time, not acoustic or playback verification.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sqlalchemy import func, or_, select, update

from djlib.application.service import new_id, require
from djlib.audio.file_identity import descriptor_snapshot, path_snapshot
from djlib.domain.contracts import (
    base_form,
    base_title,
    credit_base_form,
    credit_form,
    equivalent_labels,
    label_form,
    normalize,
    recording_key,
    related_prefixes,
    split_featured,
    title_lookup,
    version_prefixes,
)
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestCreate, RequestRefresh, RequestResolution
from djlib.persistence.database import search_fold
from djlib.persistence.models import Asset, AssetRevision, FileLocation, Recording, timestamp
from djlib.persistence.request_models import RequestLedger, RequestSubmission
from djlib.sources.web import validate_url

MAX_CANDIDATES = 20
MAX_RELATED = 5
# Catalog rows read when looking a song up by title for artists credited in another order.
MAX_TITLE_ROWS = 200
MAX_LOCATIONS = 10
MAX_VERIFY_BYTES = 1024**3
MAX_VERIFY_SECONDS = 30
STATES = ("satisfied", "missing", "ambiguous", "unknown", "unavailable", "source_selected")
# Titles whose matching form is not their key's: "Me, Myself & I" is filed as "me myself i"
# and "Me Myself and I" as "me myself and i". (A tag inside a mix name, "(Antdot & Maz (BR)
# Remix)", comes after the base title, which the title lookup already finds.)
AND = re.compile(r"&|\band\b", re.IGNORECASE)


def _before_and(title: str) -> str | None:
    """The key form of a base title's words before its first "&" or "and" ("me myself"),
    which both spellings' identity keys start with; None when it has neither."""
    parts = AND.split(base_title(split_featured(title)[0]), maxsplit=1)
    return recording_key("", parts[0]).split("|")[1] if len(parts) == 2 else None


class _Verification:
    """One bounded, cached read-only verification pass, including duplicate requests."""

    def __init__(self, app):
        self.app = app
        self.started = time.monotonic()
        self.bytes_read = 0
        self.cache = {}
        self.catalog_cache = {}

    def location(self, value, expected):
        key = value, expected
        if key in self.cache:
            return self.cache[key]
        result = self._location(value, expected)
        self.cache[key] = result
        return result

    def _location(self, value, expected):
        try:
            path = self.app.workspace.authorize(value)
            before = path_snapshot(path)
            if not before.is_regular or before.is_reparse:
                return "unavailable"
            if (
                self.bytes_read + before.size > MAX_VERIFY_BYTES
                or time.monotonic() - self.started > MAX_VERIFY_SECONDS
            ):
                return "verification_limit"
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                opened = descriptor_snapshot(stream.fileno())
                if opened.signature != before.signature:
                    return "changed"
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    self.bytes_read += len(chunk)
                    if (
                        self.bytes_read > MAX_VERIFY_BYTES
                        or time.monotonic() - self.started > MAX_VERIFY_SECONDS
                    ):
                        return "verification_limit"
                    digest.update(chunk)
                after = descriptor_snapshot(stream.fileno())
            if (
                before.signature != after.signature
                or after.signature != path_snapshot(path).signature
            ):
                return "changed"
            return "verified" if digest.hexdigest() == expected else "changed"
        except AppError as error:
            return "outside_roots" if error.code == "SOURCE_NOT_ALLOWED" else "unavailable"
        except (OSError, ValueError):
            return "unavailable"


def _identity(artist, title, version=""):
    # Symbol-only artist/title labels must not all collapse to an empty identity.
    return tuple(
        normalize(value) or " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        for value in (artist, title, version)
    )


def _equivalent(labels, requested, fold=True):
    """Same song and version: labels as written, or the same credited names in any order."""
    return equivalent_labels(
        labels, (requested["artist"], requested["title"], requested["version"]), fold=fold
    )


def _load(app, request_id):
    with app.db.transaction() as session:
        ledger = require(session, RequestLedger, request_id)
        return {
            "request_id": ledger.id,
            "revision": ledger.revision,
            "name": ledger.name,
            "request": deepcopy(ledger.request),
            "items": deepcopy(ledger.items),
            "created_at": ledger.created_at,
            "updated_at": ledger.updated_at,
        }


def _revision(ledger, revision):
    if ledger["revision"] != revision:
        raise AppError(
            "REQUEST_STALE", "The set changed in the meantime; read it again, then retry.", 409
        )


def _save(app, ledger):
    with app.db.transaction() as session:
        result = session.execute(
            update(RequestLedger)
            .where(
                RequestLedger.id == ledger["request_id"],
                RequestLedger.revision == ledger["revision"],
            )
            .values(items=ledger["items"], revision=ledger["revision"] + 1, updated_at=timestamp())
        )
        if result.rowcount != 1:
            raise AppError("REQUEST_STALE", "Another change to the set came first; try again.", 409)


def _catalog_candidates(app, item, verification, recording_id=None, asset_revision_id=None):
    requested = item["input"]
    prefixes = (
        tuple(version_prefixes(requested["artist"], requested["title"], requested["version"]))
        if requested["kind"] == "named"
        else None
    )
    # Credited names decide the title lookup below: "A & B" and "A B" share prefixes only.
    credits = (
        (
            credit_base_form(requested["artist"], requested["title"]),
            credit_form(requested["artist"], requested["title"], requested["version"]),
        )
        if prefixes
        else None
    )
    cache_key = prefixes, credits, recording_id, asset_revision_id
    if cache_key in verification.catalog_cache:
        return deepcopy(verification.catalog_cache[cache_key])
    with app.db.transaction() as session:
        statement = (
            select(Recording, AssetRevision)
            .join(Asset, Asset.recording_id == Recording.id)
            .join(AssetRevision, AssetRevision.asset_id == Asset.id)
        )
        if recording_id:
            statement = statement.where(Recording.id == recording_id)
            if asset_revision_id:
                statement = statement.where(AssetRevision.id == asset_revision_id)
        else:
            statement = statement.where(
                or_(
                    *(
                        Recording.identity_key.startswith(prefix, autoescape=True)
                        for prefix in prefixes
                    )
                )
            )
        rows = session.execute(
            statement.order_by(Recording.id, AssetRevision.id).limit(MAX_CANDIDATES + 1)
        ).all()
        truncated = len(rows) > MAX_CANDIDATES
        rows = rows[:MAX_CANDIDATES]
        if prefixes and not recording_id:
            # Untagged files carry byte-based identities; their file-name labels can still
            # match a request. They are reported as such (identity_match file_name_labels).
            seen = {revision.id for _, revision in rows}
            wanted_form = label_form(requested["artist"], requested["title"], requested["version"])
            named = session.execute(
                select(Recording, AssetRevision)
                .join(Asset, Asset.recording_id == Recording.id)
                .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                .where(
                    Recording.identity_key.startswith("provisional:"),
                    func.djlib_fold(Recording.artist) == search_fold(requested["artist"]),
                )
                .order_by(Recording.id, AssetRevision.id)
                .limit(MAX_CANDIDATES)
            ).all()
            rows += [
                row
                for row in named
                if row[1].id not in seen
                and label_form(row[0].artist, row[0].title, row[0].version) == wanted_form
            ][: max(0, MAX_CANDIDATES - len(rows))]
        context = []
        if prefixes and not recording_id:
            # Tracklists credit the same artists in another order or with other separators
            # ("Max Dean, Luke Dean, Jamie Jones" for tags "Jamie Jones, Max Dean, Luke Dean"),
            # which the artist-first prefixes above cannot find. Look the song up by its base
            # title in the identity key, then keep rows whose credited names and base title
            # are equal; a subset of the names is a different song.
            fragments, names = title_lookup(requested["artist"], requested["title"])
            seen = {revision.id for _, revision in rows}
            titled = session.execute(
                select(Recording, AssetRevision)
                .join(Asset, Asset.recording_id == Recording.id)
                .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                .where(
                    or_(
                        *(
                            Recording.identity_key.contains(fragment, autoescape=True)
                            for fragment in fragments
                        )
                    ),
                    *(Recording.identity_key.contains(name, autoescape=True) for name in names),
                )
                .order_by(Recording.id, AssetRevision.id)
                .limit(MAX_TITLE_ROWS)
            ).all()
            if names and (head := _before_and(requested["title"])) is not None:
                # Its key may spell the title another way: look the song up by the words they
                # share (by its artists alone when the title starts with "&" or "and").
                shared = [f"|{head} "] if head else []
                titled += session.execute(
                    select(Recording, AssetRevision)
                    .join(Asset, Asset.recording_id == Recording.id)
                    .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                    .where(
                        *(
                            Recording.identity_key.contains(fragment, autoescape=True)
                            for fragment in [*shared, *names]
                        )
                    )
                    .order_by(Recording.id, AssetRevision.id)
                    .limit(MAX_TITLE_ROWS)
                ).all()
            matching = []
            for row in titled:
                labels = row[0].artist, row[0].title, row[0].version
                if row[1].id in seen:
                    continue
                seen.add(row[1].id)
                if _equivalent(labels, requested):
                    matching.append(row)
                elif credit_base_form(row[0].artist, row[0].title) == credits[0]:
                    context.append(row)  # another version of this song; shown, never owned
            room = MAX_CANDIDATES - len(rows)
            # Same truncation rule as above: unseen matches mean the choice is incomplete.
            truncated = truncated or len(matching) > room
            rows += matching[:room]
        if prefixes and not recording_id:
            # Other versions you own are shown for context; they never affect matching,
            # ambiguity or the candidate bound above.
            seen = {revision.id for _, revision in rows}
            related = session.execute(
                select(Recording, AssetRevision)
                .join(Asset, Asset.recording_id == Recording.id)
                .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                .where(
                    or_(
                        *(
                            Recording.identity_key.startswith(prefix, autoescape=True)
                            for prefix in related_prefixes(requested["artist"], requested["title"])
                        )
                    )
                )
                .order_by(Recording.id, AssetRevision.id)
                .limit(MAX_RELATED + len(seen))
            ).all()
            related = [
                row
                for row in related
                if row[1].id not in seen
                and base_form(row[0].artist, row[0].title)
                == base_form(requested["artist"], requested["title"])
            ]
            seen |= {revision.id for _, revision in related}
            rows += (related + [row for row in context if row[1].id not in seen])[:MAX_RELATED]
        candidates = []
        for recording, revision in rows:
            locations = list(
                session.scalars(
                    select(FileLocation.path)
                    .where(FileLocation.revision_id == revision.id)
                    .order_by(FileLocation.managed.desc(), FileLocation.path)
                    .limit(MAX_LOCATIONS + 1)
                )
            )
            candidates.append(
                {
                    "recording_id": recording.id,
                    "asset_revision_id": revision.id,
                    "artist": recording.artist,
                    "title": recording.title,
                    "version": recording.version,
                    "sha256": revision.sha256,
                    "file_name_labels": recording.identity_key.startswith("provisional:"),
                    "locations": locations[:MAX_LOCATIONS],
                    "locations_truncated": len(locations) > MAX_LOCATIONS,
                }
            )
    result = candidates, truncated
    verification.catalog_cache[cache_key] = deepcopy(result)
    return result


# Files that may be the song under a shorter or longer artist credit, offered for the DJ to
# confirm ("maybe yours"); never owned on their own.
MAX_MAYBE = 3


def _starts(short: str, long: str) -> bool:
    """One credited name spelled out further: "robert glasper" starts "robert glasper
    experiment" (two words or more, so "the" never starts "the martinez brothers")."""
    first, second = sorted((short.split(), long.split()), key=len)
    return first == second or (len(first) >= 2 and second[: len(first)] == first)


def similar_credits(labels: tuple[str, str, str], requested: dict) -> bool:
    """The same title and mix credited to overlapping artists: one name the start of the
    other ("Robert Glasper" for "Robert Glasper Experiment"), or some of the names
    ("Kaytranada" for "Kaytranada feat. Syd"). Never the same credits, which match outright,
    and never another mix."""
    mine = credit_form(*labels)
    theirs = credit_form(requested["artist"], requested["title"], requested["version"])
    if mine[1] != theirs[1] or mine[0] == theirs[0] or not mine[0] or not theirs[0]:
        return False
    fewer, more = sorted((mine[0], theirs[0]), key=len)
    return all(any(_starts(name, other) for other in more) for name in fewer)


def _maybe(app, requested: dict, verification) -> list[dict]:
    """Catalog files that may be a missing song under other credits (``similar_credits``), as
    candidates with ``identity_match`` similar_credits; not hash-checked, never accepted."""
    key = ("maybe", requested["artist"], requested["title"], requested["version"])
    if key in verification.catalog_cache:
        return deepcopy(verification.catalog_cache[key])
    fragments, names = title_lookup(requested["artist"], requested["title"])
    # The first two words of each name: "robert glasper" finds "Robert Glasper Experiment".
    stems = {" ".join(name.split()[:2]) for name in names}
    found = []
    if stems:
        with app.db.transaction() as session:
            rows = session.execute(
                select(Recording, AssetRevision)
                .join(Asset, Asset.recording_id == Recording.id)
                .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                .where(
                    or_(
                        *(
                            Recording.identity_key.contains(fragment, autoescape=True)
                            for fragment in fragments
                        )
                    ),
                    or_(
                        *(Recording.identity_key.contains(stem, autoescape=True) for stem in stems)
                    ),
                )
                .order_by(Recording.id, AssetRevision.id)
                .limit(MAX_TITLE_ROWS)
            ).all()
            for recording, revision in rows:
                labels = recording.artist, recording.title, recording.version
                if not similar_credits(labels, requested):
                    continue
                location = session.scalar(
                    select(FileLocation.path)
                    .where(FileLocation.revision_id == revision.id)
                    .order_by(FileLocation.managed.desc(), FileLocation.path)
                    .limit(1)
                )
                found.append(
                    {
                        "recording_id": recording.id,
                        "asset_revision_id": revision.id,
                        "artist": recording.artist,
                        "title": recording.title,
                        "version": recording.version,
                        "sha256": revision.sha256,
                        "identity_match": "similar_credits",
                        "availability": "not_checked",
                        "path": location,
                    }
                )
                if len(found) == MAX_MAYBE:
                    break
    verification.catalog_cache[key] = deepcopy(found)
    return found


def _also(requested: dict) -> list[dict]:
    """A named item's other spellings (``also``) as request labels, in order."""
    credits = requested.get("also") or [] if requested["kind"] == "named" else []
    return [
        {
            "kind": "named",
            "artist": credit["artist"],
            "title": credit["title"],
            "version": credit.get("version") or "",
            "source": credit.get("source") or "",
        }
        for credit in credits
    ]


def _label(labels: dict) -> str:
    text = f"{labels['artist']} - {labels['title']}"
    return f"{text} ({labels['version']})" if labels["version"] else text


def _refresh_item(app, item, verification):
    item = deepcopy(item)
    requested, resolution = item["input"], item.get("resolution")
    selected = resolution if resolution and resolution["action"] == "satisfy" else None
    item.update(candidates=[], candidates_truncated=False, accepted=None, refreshed_at=timestamp())
    if requested["kind"] == "unknown" and not selected:
        item["state"] = "source_selected" if item.get("source_selection") else "unknown"
        return item
    recording_id = selected["recording_id"] if selected else None
    revision_id = selected.get("asset_revision_id") if selected else None
    candidates, truncated = _catalog_candidates(app, item, verification, recording_id, revision_id)
    exact = _matches(candidates, requested, verification)
    matched_as = None
    for credit in [] if exact else _also(requested):
        # A song shown under Shazam's credits ("Robert Glasper Experiment") may be filed under
        # a listing's ("Robert Glasper"): the same exact rule, for each spelling in turn.
        found, more = _catalog_candidates(
            app, {"input": credit}, verification, recording_id, revision_id
        )
        exact = _matches(found, credit, verification)
        hits = {candidate["asset_revision_id"] for candidate in exact}
        candidates = [c for c in candidates if c["asset_revision_id"] not in hits]
        seen = {candidate["asset_revision_id"] for candidate in candidates}
        candidates += [c for c in found if c["asset_revision_id"] not in seen]
        truncated = truncated or more
        if exact:
            matched_as = credit
            break
    if not exact and selected and candidates and requested["kind"] == "named":
        # The DJ confirmed a file credited a little differently ("maybe yours"): owned under
        # that file's own spelling, which is the same title and mix (similar_credits).
        chosen = candidates[0]
        labels = chosen["artist"], chosen["title"], chosen["version"]
        if similar_credits(labels, requested):
            credit = {
                "kind": "named",
                "artist": labels[0],
                "title": labels[1],
                "version": labels[2] or "",
                "source": "you",
            }
            found, more = _catalog_candidates(
                app, {"input": credit}, verification, recording_id, revision_id
            )
            exact = _matches(found, credit, verification)
            candidates, truncated = found, truncated or more
            matched_as = credit if exact else None
    item["candidates"], item["candidates_truncated"] = candidates, truncated
    eligible = [c for c in exact if c["availability"] == "verified"]
    if selected and selected.get("asset_revision_id"):
        eligible = [c for c in eligible if c["asset_revision_id"] == selected["asset_revision_id"]]
    # Do not choose among alternate byte revisions automatically, including unavailable ones.
    unambiguous = selected and selected.get("asset_revision_id") or len(exact) == 1
    if len(eligible) == 1 and unambiguous and not truncated:
        item["accepted"] = {
            **eligible[0],
            "basis": "operator_selection" if selected else "exact_catalog_labels",
            "verified_at": item["refreshed_at"],
            "scope": "catalog_labels_and_local_sha256; not_acoustic_or_playback_verification",
        }
        if matched_as:
            item["accepted"]["matched_as"] = _label(matched_as)
            item["accepted"]["matched_as_source"] = matched_as["source"]
        item["state"] = "satisfied"
    elif truncated or (len(exact) > 1 and not selected) or len(eligible) > 1:
        item["state"] = "ambiguous"
    elif exact:
        item["state"] = "unavailable"
    else:
        item["state"] = "missing" if requested["kind"] == "named" else "unknown"
        if requested["kind"] == "named" and not selected:
            seen = {candidate["asset_revision_id"] for candidate in candidates}
            maybe = [
                c
                for c in _maybe(app, requested, verification)
                if c["asset_revision_id"] not in seen
            ]
            item["candidates"] = candidates + maybe
    if item.get("source_selection") and item["state"] not in {"satisfied", "ambiguous"}:
        item["state"] = "source_selected"
    return item


def _matches(candidates: list[dict], requested: dict, verification) -> list[dict]:
    """Mark how each candidate's labels compare with ``requested`` (``identity_match``) and
    hash-check the ones naming the same song and version, which are returned.

    A file spelled as asked ("Me, Myself & I") is the match even beside a copy spelled "Me
    Myself and I"; that one counts only when no closer spelling is owned (``match_words``)."""
    wanted = (
        _identity(requested["artist"], requested["title"], requested["version"])
        if requested["kind"] == "named"
        else None
    )
    wanted_base = (
        credit_base_form(requested["artist"], requested["title"]) if wanted is not None else None
    )
    matched = []
    for candidate in candidates:
        labels = candidate["artist"], candidate["title"], candidate["version"]
        identical = wanted is not None and _identity(*labels) == wanted
        equivalent = wanted is not None and not identical and _equivalent(labels, requested)
        matches = wanted is None or identical or equivalent
        from_file_name = candidate.pop("file_name_labels", False)
        candidate["identity_match"] = (
            "operator_identified"
            if wanted is None
            else "file_name_labels"
            if from_file_name and (identical or equivalent)
            else "exact_labels"
            if identical
            else "equivalent_labels"
            if equivalent
            else "different_version"
            if _identity(candidate["artist"], candidate["title"])[:2] == wanted[:2]
            or base_form(candidate["artist"], candidate["title"])
            == base_form(requested["artist"], requested["title"])
            or credit_base_form(candidate["artist"], candidate["title"]) == wanted_base
            else "different_labels"
        )
        locations = candidate.pop("locations")
        candidate.update(availability="not_checked", path=None)
        if matches:
            close = not equivalent or _equivalent(labels, requested, fold=False)
            matched.append((close, candidate, locations))
    if any(close for close, _, _ in matched):
        matched = [entry for entry in matched if entry[0]]
    exact = []
    for _, candidate, locations in matched:
        statuses = []
        for location in locations:
            availability = verification.location(location, candidate["sha256"])
            statuses.append(availability)
            if availability == "verified":
                candidate.update(availability=availability, path=location)
                break
        else:
            candidate["availability"] = (
                "verification_limit"
                if "verification_limit" in statuses
                else "changed"
                if "changed" in statuses
                else "unavailable"
            )
        exact.append(candidate)
    return exact


def _summary(ledger):
    counts = Counter(item["state"] for item in ledger["items"])
    return {
        "request_id": ledger["request_id"],
        "revision": ledger["revision"],
        "name": ledger["name"],
        "created_at": ledger["created_at"],
        "updated_at": ledger["updated_at"],
        "total_items": len(ledger["items"]),
        "counts": {state: counts[state] for state in STATES},
        "unresolved_items": len(ledger["items"]) - counts["satisfied"],
        "unique_satisfied_recording_ids": sorted(
            {item["accepted"]["recording_id"] for item in ledger["items"] if item["accepted"]}
        ),
        "catalog_snapshot_only": True,
        "automatic_acquisition": False,
        "acoustic_identification": False,
        "app_import_verified": False,
        "device_export_verified": False,
        "hardware_verified": False,
        "verification_limits": {
            "max_bytes_per_refresh": MAX_VERIFY_BYTES,
            "max_seconds_per_refresh": MAX_VERIFY_SECONDS,
            "max_candidates_per_item": MAX_CANDIDATES,
        },
    }


def get_request(app, request_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Read a bounded stored report. This never refreshes filesystem evidence implicitly."""
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise AppError("REQUEST_PAGE_INVALID", "Use offset >= 0 and a limit from 1 to 100.")
    from djlib.application.catalog_paging import crate_of

    ledger = _load(app, request_id)
    page = ledger["items"][offset : offset + limit]
    with app.db.transaction() as session:
        crate = crate_of(session, request_id)
    return {
        **_summary(ledger),
        # Where the set came from (its page, the MixesDB tracklist it uses), when known.
        "source": ledger["request"].get("source"),
        "mixesdb": ledger["request"].get("mixesdb"),
        # Tracks the copied tracklist left out: the list is partial.
        "hidden_tracks": ledger["request"].get("hidden_tracks"),
        # The newest crate made from it: {collection_id, name, created_at, tracks}.
        "crate": crate,
        "items": page,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(ledger["items"]) else None,
    }


def create_request(app, request: RequestCreate, same_list: bool = False) -> dict:
    """Save a request list and check it against the catalog.

    With ``same_list``, a list saved under the same name with the same songs in the same order
    (a set checked again, perhaps by an older djlib that wrote other notes) gets this list's
    notes and evidence in place, under a new revision, and is returned as reused instead of a
    second list (and crate, and playlist) beside it.
    """
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    digest = _request_hash(payload)
    with app.db.transaction() as session:
        previous = session.get(RequestSubmission, request.idempotency_key)
        if previous:
            if previous.request_hash != digest:
                raise AppError(
                    "IDEMPOTENCY_CONFLICT", "This key belongs to a different song request.", 409
                )
            existing = previous.ledger_id
        else:
            existing = None
    if same_list:
        existing = _same_list(app, payload, request.idempotency_key, digest, existing) or existing
    if existing:
        # The same list again: the stored report, marked so callers can re-check it.
        return {**get_request(app, existing), "reused": True}
    verification, seen, items = _Verification(app), {}, []
    for position, value in enumerate(payload["items"], 1):
        item_id = new_id("requested")
        identity = (
            _identity(value["artist"], value["title"], value["version"])
            if value["kind"] == "named"
            else None
        )
        item = {
            "item_id": item_id,
            "position": position,
            "input": value,
            "duplicate_of": seen.get(identity) if identity else None,
            "resolution": None,
            "source_selection": None,
        }
        if identity:
            seen.setdefault(identity, item_id)
        items.append(_refresh_item(app, item, verification))
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        previous = session.get(RequestSubmission, request.idempotency_key)
        if previous:
            if previous.request_hash != digest:
                raise AppError(
                    "IDEMPOTENCY_CONFLICT", "This key belongs to a different song request.", 409
                )
            request_id = previous.ledger_id
        else:
            request_id = new_id("request")
            session.add(
                RequestLedger(id=request_id, name=request.name, request=payload, items=items)
            )
            session.flush()
            session.add(
                RequestSubmission(
                    key=request.idempotency_key, request_hash=digest, ledger_id=request_id
                )
            )
    return get_request(app, request_id)


# Where a set came from (and what its copied tracklist left out): shown with it, never part
# of what its key names.
SET_PAGE = ("source", "mixesdb", "hidden_tracks")


def _request_hash(payload: dict) -> str:
    """The digest a key's request is held to: the name and items, not the set's page."""
    kept = {key: value for key, value in payload.items() if key not in SET_PAGE}
    return hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()


def _songs(items: list[dict]) -> list:
    """What makes two lists the same set: each song's credited names, title and version as
    request matching compares them (case, "feat." or "ft." and the order of names aside), and
    where its IDs are. Notes, times, links, other spellings and how sure djlib is are evidence:
    a set checked again with a tracklist pasted from 1001Tracklists ("Full Crate ft. Papa
    Ghana - L'Afrique" for MixesDB's "Feat. … L'afrique") is the same set."""
    return [
        credit_form(item["artist"], item["title"], item["version"])
        if item["kind"] == "named"
        else None
        for item in items
    ]


def _same_list(app, payload: dict, key: str, digest: str, ledger_id: str | None) -> str | None:
    """The saved list ``payload`` repeats (``ledger_id``, else the newest one with its name and
    songs), with each item's input updated to ``payload``'s; ``key`` then names it.

    Item IDs, operator choices and selected sources stay; a refresh re-checks the evidence. A
    list of IDs alone names no song, so two sets with one title can't be told apart: only its
    key finds it.
    """
    songs = _songs(payload["items"])
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        previous = session.get(RequestSubmission, key)
        if previous:
            if previous.request_hash != digest:
                raise AppError(
                    "IDEMPOTENCY_CONFLICT", "This key belongs to a different song request.", 409
                )
            ledger_id = previous.ledger_id
        named = (
            filter(None, [session.get(RequestLedger, ledger_id)])
            if ledger_id
            else session.scalars(
                select(RequestLedger)
                .where(RequestLedger.name == payload["name"])
                .order_by(RequestLedger.updated_at.desc(), RequestLedger.id.desc())
            )
            if any(songs)
            else []
        )
        ledger = next(
            (each for each in named if _songs([item["input"] for item in each.items]) == songs),
            None,
        )
        if ledger is None:
            return None
        if not previous:
            session.add(RequestSubmission(key=key, request_hash=digest, ledger_id=ledger.id))
        items, changed = deepcopy(ledger.items), False
        for item, value in zip(items, payload["items"], strict=True):
            if item["input"] != value:
                item["input"], changed = value, True
        # Checked again: it comes first among the sets and is what `last` names, even when
        # nothing changed (the revision moves only with its items, so printed commands hold).
        ledger.updated_at = timestamp()
        if changed:
            ledger.items, ledger.request = items, payload
            ledger.revision += 1
        elif any(ledger.request.get(key) != payload.get(key) for key in SET_PAGE):
            # Where the set came from, learnt by a newer djlib: the songs are as they were.
            ledger.request = {
                **ledger.request,
                **{key: payload[key] for key in SET_PAGE if key in payload},
            }
        return ledger.id


def refresh_request(app, request_id: str, request: RequestRefresh) -> dict:
    ledger = _load(app, request_id)
    _revision(ledger, request.revision)
    verification = _Verification(app)
    selected = set(request.item_ids) if request.item_ids is not None else None
    if selected is not None and selected - {item["item_id"] for item in ledger["items"]}:
        raise AppError("NOT_FOUND", "One or more requested song items were not found.", 404)
    before = _evidence(ledger["items"])
    ledger["items"] = [
        _refresh_item(app, item, verification)
        if selected is None or item["item_id"] in selected
        else item
        for item in ledger["items"]
    ]
    # A re-check that changed nothing keeps the revision, so printed commands stay valid; it
    # still counts as the set's latest check.
    if _evidence(ledger["items"]) != before:
        _save(app, ledger)
    else:
        with app.db.transaction() as session:
            session.execute(
                update(RequestLedger)
                .where(RequestLedger.id == request_id)
                .values(updated_at=timestamp())
            )
    return get_request(app, request_id)


def _evidence(items: list[dict]) -> list:
    """What a refresh can change about a request list, without check timestamps."""
    return [
        (
            item["item_id"],
            item.get("state"),
            (item.get("accepted") or {}).get("asset_revision_id"),
            sorted(
                (
                    candidate.get("asset_revision_id") or "",
                    candidate.get("identity_match") or "",
                    candidate.get("availability") or "",
                )
                for candidate in item.get("candidates") or []
            ),
        )
        for item in items
    ]


def resolve_request(app, request_id: str, item_id: str, request: RequestResolution) -> dict:
    ledger = _load(app, request_id)
    _revision(ledger, request.revision)
    item = next((item for item in ledger["items"] if item["item_id"] == item_id), None)
    if item is None:
        raise AppError("NOT_FOUND", "The requested song item was not found.", 404)
    decision = request.model_dump(mode="json", exclude={"revision"})
    decision["selected_at"] = timestamp()
    if request.action == "select_source":
        validate_url(request.source_url)
        item["source_selection"] = {**decision, "verification": "selected_only_not_acquired"}
        item["resolution"] = None
    elif request.action == "clear":
        item["resolution"] = item["source_selection"] = None
    else:
        with app.db.transaction() as session:
            recording = require(session, Recording, request.recording_id)
            value = item["input"]
            labels = recording.artist, recording.title, recording.version
            if (
                value["kind"] == "named"
                and not any(_equivalent(labels, spelling) for spelling in (value, *_also(value)))
                and not similar_credits(labels, value)
            ):
                raise AppError(
                    "REQUEST_IDENTITY_CONFLICT",
                    "The selected recording is a different artist, title, or version.",
                    409,
                )
        item["resolution"] = decision
    refreshed = _refresh_item(app, item, _Verification(app))
    if request.action == "satisfy" and refreshed["state"] != "satisfied":
        raise AppError(
            "REQUEST_RECORDING_UNAVAILABLE",
            "That file can't be used: it changed or isn't there any more. Check the set "
            "again, then pick again.",
            409,
        )
    ledger["items"][item["position"] - 1] = refreshed
    _save(app, ledger)
    return get_request(app, request_id)


def export_missing_report(app, request_id: str, revision: int) -> dict:
    """Atomically publish a unique JSON report under workspace exports; never overwrite."""
    ledger = _load(app, request_id)
    _revision(ledger, revision)
    unresolved = [item for item in ledger["items"] if item["state"] != "satisfied"]
    report = {**_summary(ledger), "report_kind": "unresolved_song_requests", "items": unresolved}
    directory = app.workspace.exports / "requests" / ledger["request_id"]
    if not directory.resolve().is_relative_to(app.workspace.root):
        raise AppError("REPORT_PATH_INVALID", "Request reports must remain inside this workspace.")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"missing-r{revision}-{new_id('report')}.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".request-report-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "request_id": request_id,
        "revision": revision,
        "report_path": str(destination),
        "unresolved_items": len(unresolved),
    }


def collect_request(app, request_id: str, revision: int, name: str | None = None) -> dict:
    """Queue an ordered collection of the owned tracks in a request list, in list order.

    Only items already ``satisfied`` by a hash-verified catalog revision are included;
    missing, ambiguous and unknown entries stay in the request for later acquisition.
    """
    from djlib.application.organization import organize_collection
    from djlib.domain.organization_contracts import OrganizationRequest, RecordingReference

    ledger = _load(app, request_id)
    _revision(ledger, revision)
    references, seen = [], set()
    for item in ledger["items"]:
        accepted = item.get("accepted") if item.get("state") == "satisfied" else None
        if not accepted or accepted["asset_revision_id"] in seen:
            continue
        seen.add(accepted["asset_revision_id"])
        references.append(
            RecordingReference(
                recording_id=accepted["recording_id"],
                asset_revision_id=accepted["asset_revision_id"],
            )
        )
    if not references:
        raise AppError(
            "COLLECTION_EMPTY",
            "None of these songs are owned yet. Add music, then check the set again.",
        )
    title = (name or ledger["name"]).strip()
    # Keyed by name and owned tracks, not revision: re-checking an unchanged list reuses its
    # crate, while a list that gained or lost songs gets a new one.
    owned = "\n".join(f"{ref.recording_id}:{ref.asset_revision_id}" for ref in references)
    digest = hashlib.sha256(f"{title}\n{owned}".encode()).hexdigest()[:16]
    return organize_collection(
        app,
        OrganizationRequest(
            name=title,
            tracks=references,
            unknown="include",
            idempotency_key=f"request-collection:{request_id}:{digest}",
        ),
    )
