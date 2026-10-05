"""Explicit, durable reconciliation of changed indexed paths; never edit media.

Collections and annotations remain attached to their original byte revisions.
This is exact payload comparison, not recording recognition or identity repair.
"""

import asyncio
import hashlib
import json
from copy import deepcopy

from sqlalchemy import select

from djlib.application.service import add_event, new_id, require
from djlib.audio.catalog_inspection import inspect_catalog_audio
from djlib.audio.file_identity import path_snapshot
from djlib.domain.errors import AppError
from djlib.domain.reconciliation_contracts import ReconcileRequest
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Delivery,
    FileLocation,
    Job,
    JobItem,
    Recording,
    Submission,
    timestamp,
)
from djlib.persistence.request_models import RequestLedger


def reconcile_files(app, request: ReconcileRequest):
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    semantic = hashlib.sha256(
        json.dumps({"kind": "reconcile", "payload": payload}, sort_keys=True).encode()
    ).hexdigest()
    # Replaying accepted intent must work after the source moves or disappears.
    # Recheck under the writer reservation below for simultaneous first submits.
    with app.db.transaction() as session:
        previous = session.get(Submission, request.idempotency_key)
        if previous:
            if previous.request_hash != semantic:
                raise AppError("IDEMPOTENCY_CONFLICT", "This key names a different request.", 409)
            previous_job_id = previous.job_id
        else:
            previous_job_id = None
    if previous_job_id:
        return app.job(previous_job_id)
    for item in payload["items"]:
        item["path"] = str(app.workspace.authorize(item["path"]))
    if len({item["path"] for item in payload["items"]}) != len(payload["items"]):
        raise AppError("RECONCILIATION_DUPLICATE", "Changed locations must be distinct.")
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        previous = session.get(Submission, request.idempotency_key)
        if previous:
            if previous.request_hash != semantic:
                raise AppError("IDEMPOTENCY_CONFLICT", "This key names a different request.", 409)
            job_id = previous.job_id
        else:
            for entry in payload["items"]:
                _location(session, entry)
            job = Job(id=new_id("job"), kind="reconcile", request=payload)
            session.add(job)
            session.flush()
            for position, entry in enumerate(payload["items"]):
                session.add(
                    JobItem(
                        id=new_id("item"),
                        job_id=job.id,
                        position=position,
                        request={"track": entry},
                    )
                )
            session.add(
                Submission(key=request.idempotency_key, request_hash=semantic, job_id=job.id)
            )
            add_event(session, job, "accepted")
            job_id = job.id
    return app.job(job_id)


def _location(session, entry):
    location = session.scalar(select(FileLocation).where(FileLocation.path == entry["path"]))
    if location is None or location.revision_id != entry["expected_asset_revision_id"]:
        raise AppError(
            "RECONCILIATION_STALE",
            "The indexed location no longer names the expected revision.",
            409,
        )
    return location


def _baseline(app, revision_id, properties, expected_hash):
    if properties.get("audio_payload"):
        return properties["audio_payload"]
    # Older catalogs have no payload baseline. Another unchanged location can
    # establish one, but labels/duration alone must never prove a tag-only edit.
    with app.db.transaction() as session:
        locations = list(
            session.scalars(
                select(FileLocation.path).where(FileLocation.revision_id == revision_id)
            )
        )
    for value in locations:
        try:
            path = app.workspace.authorize(value)
            inspection, payload, _ = inspect_catalog_audio(path)
            if inspection.sha256 == expected_hash:
                return payload
        except (AppError, OSError, ValueError):
            continue
    raise AppError(
        "AUDIO_BASELINE_UNAVAILABLE",
        "No verified original payload remains. Restore an old-byte copy or explicitly "
        "treat this path as replacement audio; tag-only equivalence cannot be guessed.",
    )


def _invalidate(session, job_id, item_id, path, old_revision, new_revision):
    marker = {
        "job_id": job_id,
        "item_id": item_id,
        "path": path,
        "old_asset_revision_id": old_revision,
        "new_asset_revision_id": new_revision,
        "at": timestamp(),
    }
    historical = {"deliveries": [], "requests": [], "exports": []}
    for delivery in session.scalars(select(Delivery)):
        if not any(
            track.get("asset_revision_id") == old_revision and track.get("path") == path
            for track in delivery.snapshot.get("tracks", [])
        ):
            continue
        historical["deliveries"].append(
            {
                "delivery_id": delivery.id,
                "revision": delivery.revision,
                "evidence": deepcopy(delivery.evidence),
            }
        )
        delivery.evidence = {"catalog_reconciliation": marker}
        delivery.revision += 1
        delivery.updated_at = timestamp()
    for ledger in session.scalars(select(RequestLedger)):
        items, changed = deepcopy(ledger.items), False
        for item in items:
            accepted = item.get("accepted") or {}
            if accepted.get("asset_revision_id") == old_revision and accepted.get("path") == path:
                historical["requests"].append(
                    {
                        "request_id": ledger.id,
                        "revision": ledger.revision,
                        "item_id": item.get("item_id"),
                        "accepted": accepted,
                    }
                )
                item.update(accepted=None, state="unavailable", catalog_reconciliation=marker)
                changed = True
            for candidate in item.get("candidates", []):
                if (
                    candidate.get("asset_revision_id") == old_revision
                    and candidate.get("path") == path
                ):
                    candidate.update(availability="changed", path=None)
                    changed = True
        if changed:
            ledger.items, ledger.revision, ledger.updated_at = (
                items,
                ledger.revision + 1,
                timestamp(),
            )
    for export in session.scalars(select(Job).where(Job.kind == "export")):
        if any(
            track.get("asset_revision_id") == old_revision and track.get("path") == path
            for track in export.request.get("snapshot", {}).get("tracks", [])
        ):
            export.result = {**export.result, "catalog_reconciliation": marker}
            add_event(session, export, "source_revision_invalidated", marker)
            historical["exports"].append(export.id)
    return historical


async def reconcile_item(app, job_id, generation, item_id):
    with app.db.transaction() as session:
        job, item = require(session, Job, job_id), require(session, JobItem, item_id)
        if job.state != "running" or job.generation != generation or item.state != "pending":
            return
        entry = dict(item.request["track"])
        item.state = "running"
    try:
        with app.db.transaction() as session:
            location = _location(session, entry)
            old = require(session, AssetRevision, location.revision_id)
            old_id, old_hash, old_asset_id = old.id, old.sha256, old.asset_id
            old_properties = dict(old.properties)
            old_recording_id = require(session, Asset, old.asset_id).recording_id
        path = app.workspace.authorize(entry["path"])
        inspected, payload, observed = await asyncio.to_thread(inspect_catalog_audio, path)
        if inspected.sha256 != entry["expected_sha256"]:
            raise AppError(
                "RECONCILIATION_BYTES_CHANGED", "Current bytes differ from the requested hash.", 409
            )
        unchanged = inspected.sha256 == old_hash
        if entry["action"] == "tag_only" and not unchanged:
            baseline = await asyncio.to_thread(_baseline, app, old_id, old_properties, old_hash)
            measured = inspected.as_dict()
            format_changed = (
                any(
                    measured.get(key) != old_properties.get(key)
                    for key in ("codec", "sample_rate", "channels", "bit_depth")
                )
                or abs(inspected.duration_seconds - old_properties["duration_seconds"]) > 0.01
            )
            if baseline != payload or format_changed:
                raise AppError(
                    "AUDIO_CHANGED",
                    "Decoded audio or its stream format changed; this is not a tag-only edit.",
                )
        with app.db.transaction() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job, item = require(session, Job, job_id), require(session, JobItem, item_id)
            if job.state != "running" or job.generation != generation or item.state != "running":
                return
            location = _location(session, entry)
            if path_snapshot(path).signature != observed.signature:
                raise AppError(
                    "FILE_CHANGED", "The file changed before reconciliation committed.", 409
                )
            old = require(session, AssetRevision, old_id)
            revision = session.scalar(
                select(AssetRevision).where(AssetRevision.sha256 == inspected.sha256)
            )
            if revision:
                asset = require(session, Asset, revision.asset_id)
                if entry["action"] == "tag_only" and asset.recording_id != old_recording_id:
                    raise AppError(
                        "EXISTING_IDENTITY_CONFLICT",
                        "These bytes already have another recording identity.",
                        409,
                    )
            else:
                if entry["action"] == "tag_only":
                    asset = require(session, Asset, old_asset_id)
                else:
                    key = f"provisional:sha256:{inspected.sha256}"
                    recording = session.scalar(
                        select(Recording).where(Recording.identity_key == key)
                    )
                    if not recording:
                        recording = Recording(
                            id=new_id("rec"),
                            identity_key=key,
                            artist=inspected.artist or "Unknown artist",
                            title=inspected.title or path.stem,
                            version="",
                            evidence={
                                "method": "provisional_bytes",
                                "labels_are_identity": False,
                                "acoustic_identity_verified": False,
                            },
                        )
                        session.add(recording)
                        session.flush()
                    asset = Asset(
                        id=new_id("asset"),
                        recording_id=recording.id,
                        provenance={
                            "kind": "explicit_reconciliation",
                            "source": str(path),
                            "previous_asset_revision_id": old_id,
                        },
                    )
                    session.add(asset)
                    session.flush()
                revision = AssetRevision(
                    id=new_id("rev"),
                    asset_id=asset.id,
                    sha256=inspected.sha256,
                    properties={
                        **inspected.as_dict(),
                        "audio_payload": payload,
                        "indexed_path": str(path),
                        "provenance": {
                            "kind": "explicit_reconciliation",
                            "action": entry["action"],
                            "previous_asset_revision_id": old_id,
                            "job_id": job_id,
                        },
                    },
                )
                session.add(revision)
                session.flush()
            invalidated = {"deliveries": [], "requests": [], "exports": []}
            if not unchanged:
                old.properties = {**old.properties, "last_known_path": str(path)}
                location.revision_id = revision.id
                invalidated = _invalidate(session, job_id, item_id, str(path), old_id, revision.id)
            item.state = "succeeded"
            item.result = {
                "action": "unchanged" if unchanged else entry["action"],
                "path": str(path),
                "old_recording_id": old_recording_id,
                "recording_id": asset.recording_id,
                "old_asset_revision_id": old_id,
                "asset_revision_id": revision.id,
                "sha256": inspected.sha256,
                "invalidated_evidence": invalidated,
                "memberships_upgraded": False,
                "annotations_copied": False,
                "source_modified": False,
            }
            add_event(
                session,
                job,
                "item_reconciled",
                {"item_id": item_id, "asset_revision_id": revision.id},
            )
    except (AppError, OSError, ValueError) as exc:
        error = (
            exc
            if isinstance(exc, AppError)
            else AppError("FILE_UNAVAILABLE", "The file could not be inspected safely.")
        )
        with app.db.transaction() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job, item = require(session, Job, job_id), require(session, JobItem, item_id)
            if job.state == "running" and job.generation == generation and item.state == "running":
                item.state, item.result = "failed", {"error": error.as_dict()}
                add_event(session, job, "item_failed", {"item_id": item_id, "code": error.code})
