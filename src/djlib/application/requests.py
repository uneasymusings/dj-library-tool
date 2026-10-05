"""Persistent song requests with conservative catalog reuse and explicit unresolved evidence.

No provider calls, acquisition jobs, native app changes, or device writes happen here.
Saved matches are observations at refresh time, not acoustic or playback verification.
"""

import hashlib
import json
import os
import tempfile
import time
import unicodedata
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sqlalchemy import or_, select, update

from djlib.application.service import new_id, require
from djlib.audio.file_identity import descriptor_snapshot, path_snapshot
from djlib.domain.contracts import (
    base_form,
    label_form,
    normalize,
    related_prefixes,
    version_prefixes,
)
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestCreate, RequestRefresh, RequestResolution
from djlib.persistence.models import Asset, AssetRevision, FileLocation, Recording, timestamp
from djlib.persistence.request_models import RequestLedger, RequestSubmission
from djlib.sources.web import validate_url

MAX_CANDIDATES = 20
MAX_RELATED = 5
MAX_LOCATIONS = 10
MAX_VERIFY_BYTES = 1024**3
MAX_VERIFY_SECONDS = 30
STATES = ("satisfied", "missing", "ambiguous", "unknown", "unavailable", "source_selected")


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
            "REQUEST_STALE", "Read the current request revision before changing it.", 409
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
            raise AppError("REQUEST_STALE", "Another update changed the request evidence.", 409)


def _catalog_candidates(app, item, verification, recording_id=None, asset_revision_id=None):
    requested = item["input"]
    prefixes = (
        tuple(version_prefixes(requested["artist"], requested["title"], requested["version"]))
        if requested["kind"] == "named"
        else None
    )
    cache_key = prefixes, recording_id, asset_revision_id
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
            rows += [
                row
                for row in related
                if row[1].id not in seen
                and base_form(row[0].artist, row[0].title)
                == base_form(requested["artist"], requested["title"])
            ][:MAX_RELATED]
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
                    "locations": locations[:MAX_LOCATIONS],
                    "locations_truncated": len(locations) > MAX_LOCATIONS,
                }
            )
    result = candidates, truncated
    verification.catalog_cache[cache_key] = deepcopy(result)
    return result


def _refresh_item(app, item, verification):
    item = deepcopy(item)
    requested, resolution = item["input"], item.get("resolution")
    selected = resolution if resolution and resolution["action"] == "satisfy" else None
    item.update(candidates=[], candidates_truncated=False, accepted=None, refreshed_at=timestamp())
    if requested["kind"] == "unknown" and not selected:
        item["state"] = "source_selected" if item.get("source_selection") else "unknown"
        return item
    candidates, truncated = _catalog_candidates(
        app,
        item,
        verification,
        selected["recording_id"] if selected else None,
        selected.get("asset_revision_id") if selected else None,
    )
    wanted = (
        _identity(requested["artist"], requested["title"], requested["version"])
        if requested["kind"] == "named"
        else None
    )
    exact = []
    for candidate in candidates:
        labels = candidate["artist"], candidate["title"], candidate["version"]
        identical = wanted is not None and _identity(*labels) == wanted
        equivalent = (
            wanted is not None
            and not identical
            and label_form(*labels)
            == label_form(requested["artist"], requested["title"], requested["version"])
        )
        matches = wanted is None or identical or equivalent
        candidate["identity_match"] = (
            "operator_identified"
            if wanted is None
            else "exact_labels"
            if identical
            else "equivalent_labels"
            if equivalent
            else "different_version"
            if _identity(candidate["artist"], candidate["title"])[:2] == wanted[:2]
            or base_form(candidate["artist"], candidate["title"])
            == base_form(requested["artist"], requested["title"])
            else "different_labels"
        )
        locations = candidate.pop("locations")
        candidate.update(availability="not_checked", path=None)
        if matches:
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
        item["state"] = "satisfied"
    elif truncated or (len(exact) > 1 and not selected) or len(eligible) > 1:
        item["state"] = "ambiguous"
    elif exact:
        item["state"] = "unavailable"
    else:
        item["state"] = "missing" if requested["kind"] == "named" else "unknown"
    if item.get("source_selection") and item["state"] not in {"satisfied", "ambiguous"}:
        item["state"] = "source_selected"
    return item


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
    ledger = _load(app, request_id)
    page = ledger["items"][offset : offset + limit]
    return {
        **_summary(ledger),
        "items": page,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(ledger["items"]) else None,
    }


def create_request(app, request: RequestCreate) -> dict:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
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
    if existing:
        return get_request(app, existing)
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


def refresh_request(app, request_id: str, request: RequestRefresh) -> dict:
    ledger = _load(app, request_id)
    _revision(ledger, request.revision)
    verification = _Verification(app)
    selected = set(request.item_ids) if request.item_ids is not None else None
    if selected is not None and selected - {item["item_id"] for item in ledger["items"]}:
        raise AppError("NOT_FOUND", "One or more requested song items were not found.", 404)
    ledger["items"] = [
        _refresh_item(app, item, verification)
        if selected is None or item["item_id"] in selected
        else item
        for item in ledger["items"]
    ]
    _save(app, ledger)
    return get_request(app, request_id)


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
            if value["kind"] == "named" and label_form(
                recording.artist, recording.title, recording.version
            ) != label_form(value["artist"], value["title"], value["version"]):
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
            "Choose one current, hash-verified catalog revision; refresh to inspect candidates.",
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
            "None of these songs are owned yet. Add music, then refresh the request list.",
        )
    title = (name or ledger["name"]).strip()
    digest = hashlib.sha256(title.encode()).hexdigest()[:12]
    return organize_collection(
        app,
        OrganizationRequest(
            name=title,
            tracks=references,
            unknown="include",
            idempotency_key=f"request-collection:{request_id}:{revision}:{digest}",
        ),
    )
