"""Bounded rediscovery with query-bound keyset cursors and an insertion cutoff."""

import base64
import hashlib
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, or_, select, tuple_

from djlib.domain.errors import AppError
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Collection,
    Delivery,
    FileLocation,
    Job,
    Membership,
    Recording,
    timestamp,
)
from djlib.persistence.request_models import RequestLedger


def _context(kind, query, limit, after):
    if (
        not isinstance(query, str)
        or len(query) > 500
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise AppError(
            "PAGE_INVALID", "Use a query up to 500 characters and a limit from 1 to 100."
        )
    scope = hashlib.sha256(json.dumps([kind, query]).encode()).hexdigest()
    if after is None:
        return {"scope": scope, "cutoff": timestamp(), "key": None}
    try:
        if not isinstance(after, str) or not 1 <= len(after) <= 2000:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
        if set(data) != {"scope", "cutoff", "key"} or data["scope"] != scope:
            raise ValueError
        if not isinstance(data["cutoff"], str) or len(data["cutoff"]) > 40:
            raise ValueError
        if datetime.fromisoformat(data["cutoff"]).tzinfo is None:
            raise ValueError
        if not isinstance(data["key"], list) or len(data["key"]) != 2:
            raise ValueError
        if any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in data["key"]):
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AppError(
            "CURSOR_INVALID", "Use the next_cursor from the same listing and query."
        ) from None


def _result(kind, rows, total, limit, context, key):
    more = len(rows) > limit
    cursor = None
    if more:
        data = {**context, "key": key(rows[limit - 1])}
        cursor = (
            base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
    return {
        kind: rows[:limit],
        "total": total,
        "limit": limit,
        "next_cursor": cursor,
        "snapshot_at": context["cutoff"],
        "snapshot_scope": "membership_created_by_cutoff; current_metadata_and_locations",
    }


def library(app, query="", limit=20, after=None):
    context = _context("library", query, limit, after)
    statement = (
        select(Recording, AssetRevision)
        .join(Asset, Asset.recording_id == Recording.id)
        .join(AssetRevision, AssetRevision.asset_id == Asset.id)
        .where(AssetRevision.created_at <= context["cutoff"])
    )
    if query:
        statement = statement.where(
            or_(
                Recording.artist.icontains(query, autoescape=True),
                Recording.title.icontains(query, autoescape=True),
                Recording.version.icontains(query, autoescape=True),
            )
        )
    with app.db.transaction() as session:
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        if context["key"]:
            statement = statement.where(
                tuple_(Recording.id, AssetRevision.id) > tuple(context["key"])
            )
        rows = session.execute(
            statement.order_by(Recording.id, AssetRevision.id).limit(limit + 1)
        ).all()
        locations = defaultdict(list)
        if rows:
            for location in session.scalars(
                select(FileLocation)
                .where(FileLocation.revision_id.in_([r.id for _, r in rows]))
                .order_by(FileLocation.managed.desc(), FileLocation.path, FileLocation.id)
            ):
                locations[location.revision_id].append(location)
        tracks = []
        for recording, revision in rows:
            candidates = locations[revision.id]
            primary = candidates[0] if candidates else None
            tracks.append(
                {
                    "recording_id": recording.id,
                    "asset_revision_id": revision.id,
                    "artist": recording.artist,
                    "title": recording.title,
                    "version": recording.version,
                    "sha256": revision.sha256,
                    "properties": revision.properties,
                    "path": primary.path if primary else None,
                    "managed": primary.managed if primary else False,
                    "identity_evidence": recording.evidence,
                    "locations": [
                        {"location_id": loc.id, "path": loc.path, "managed": loc.managed}
                        for loc in candidates
                    ],
                    "location_count": len(candidates),
                    "availability": "not_checked" if candidates else "no_recorded_location",
                    "last_known_path": revision.properties.get("last_known_path")
                    or revision.properties.get("indexed_path"),
                }
            )
    return _result(
        "tracks",
        tracks,
        total,
        limit,
        context,
        lambda r: [r["recording_id"], r["asset_revision_id"]],
    )


def saved(app, kind, query="", limit=20, after=None):
    context = _context(kind, query, limit, after)
    model, identifier = {
        "collections": (Collection, "collection_id"),
        "requests": (RequestLedger, "request_id"),
        "deliveries": (Delivery, "delivery_id"),
        "jobs": (Job, "job_id"),
    }[kind]
    name = model.request["name"].as_string() if kind in {"jobs", "deliveries"} else model.name
    columns = [model.id.label(identifier), name.label("name"), model.created_at]
    if kind == "collections":
        count = (
            select(func.count())
            .select_from(Membership)
            .where(Membership.collection_id == Collection.id)
            .scalar_subquery()
        )
        columns += [model.revision, count.label("track_count")]
    elif kind == "requests":
        columns += [
            model.revision,
            model.updated_at,
            func.json_array_length(model.items).label("total_items"),
        ]
    elif kind == "deliveries":
        columns += [
            model.revision,
            model.updated_at,
            model.job_id,
            model.request["workflow"].as_string().label("workflow"),
            model.request["phase"].as_string().label("phase"),
        ]
        for stage in (
            "imported",
            "analyzed",
            "native_exported",
            "device_library_checked",
            "hardware_playback",
        ):
            columns.append(model.evidence[stage]["outcome"].as_string().label(stage))
    else:
        columns += [model.kind, model.state, model.outcome, model.updated_at]
    statement = select(*columns).where(model.created_at <= context["cutoff"])
    if query:
        statement = statement.where(
            or_(name.icontains(query, autoescape=True), model.id.icontains(query, autoescape=True))
        )
    with app.db.transaction() as session:
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        if context["key"]:
            statement = statement.where(tuple_(model.created_at, model.id) < tuple(context["key"]))
        rows = [
            dict(row)
            for row in session.execute(
                statement.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
            ).mappings()
        ]
    for row in rows:
        if kind == "deliveries":
            row["fresh_verification_performed"] = False
            row["evidence_scope"] = (
                "stored_operator_observations; inspect_and_verify_delivery_before_use"
            )
        if kind == "collections":
            row.update(app_state="not_tracked_here", device_state="not_tracked_here")
    return _result(kind, rows, total, limit, context, lambda r: [r["created_at"], r[identifier]])
