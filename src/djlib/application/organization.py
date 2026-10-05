"""Catalog annotations and ordered collections without retagging or re-identifying audio."""

import hashlib
import json
import math
import re
from collections import Counter

import mutagen
from sqlalchemy import select, update

from djlib.application.service import add_event, new_id, require
from djlib.audio.inspection import checksum
from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest, clean_key
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Collection,
    FileLocation,
    Job,
    JobItem,
    Membership,
    Recording,
    Submission,
    timestamp,
)
from djlib.persistence.organization_models import RecordingAnnotation

ANNOTATION_FIELDS = {"notes", "genres", "tags", "set_role", "energy", "bpm", "key"}


def _catalog(session, recording_id, asset_revision_id):
    recording = require(session, Recording, recording_id)
    revision = require(session, AssetRevision, asset_revision_id)
    if require(session, Asset, revision.asset_id).recording_id != recording_id:
        raise AppError("REVISION_MISMATCH", "The byte revision belongs to another recording.")
    return recording, revision


def _annotation(row):
    return {
        "revision": row.revision if row else 0,
        "annotations": row.annotations if row else {},
        "updated_at": row.updated_at if row else None,
    }


def annotation_status(app, recording_id: str, asset_revision_id: str) -> dict:
    with app.db.transaction() as session:
        _catalog(session, recording_id, asset_revision_id)
        result = _annotation(session.get(RecordingAnnotation, asset_revision_id))
    return {"recording_id": recording_id, "asset_revision_id": asset_revision_id, **result}


def _tag_values(tags, names):
    values = []
    if tags is None:
        return values
    for name, value in tags.items():
        if str(name).casefold().split(":", 1)[0] not in names and str(name).casefold() not in names:
            continue
        entries = getattr(value, "text", value)
        if not isinstance(entries, (list, tuple)):
            entries = [entries]
        for entry in entries[:32]:
            if isinstance(entry, bytes):
                entry = entry.decode("utf-8", errors="replace")
            text = str(entry).strip()[:4000]
            if text and text not in values:
                values.append(text)
            if len(values) >= 32:
                return values
    return values


def _scalar(values, kind):
    parsed = []
    for value in values:
        try:
            number = float(value) if kind == "bpm" else clean_key(value)
            if kind == "bpm" and (not math.isfinite(number) or not 20 <= number <= 400):
                continue
            if number not in parsed:
                parsed.append(number)
        except ValueError:
            continue
    known = len(parsed) == 1 and len(values) == 1
    return {
        "value": parsed[0] if known else None,
        "known": known,
        "raw_values": values,
        "source": "embedded_tag" if values else "unknown",
        "verified": False,
        "accuracy": "unverified" if known else "unknown",
    }


def _embedded(path):
    try:
        media = mutagen.File(path)
        tags = media.tags if media is not None else None
    except (mutagen.MutagenError, ValueError, OSError) as exc:
        raise AppError("METADATA_UNREADABLE", "Embedded metadata could not be read.") from exc
    bpm = _tag_values(
        tags, {"tbpm", "bpm", "tempo", "tmpo", "txxx:bpm", "----:com.apple.itunes:bpm"}
    )
    key = _tag_values(
        tags, {"tkey", "initialkey", "key", "txxx:initialkey", "----:com.apple.itunes:initialkey"}
    )
    genres = _tag_values(tags, {"tcon", "genre", "©gen"})
    comments = _tag_values(tags, {"comm", "comment", "description", "©cmt"})
    return {
        "bpm": _scalar(bpm, "bpm"),
        "key": _scalar(key, "key"),
        "genres": {"values": genres, "source": "embedded_tag" if genres else "unknown"},
        "comments": {"values": comments, "source": "embedded_tag" if comments else "unknown"},
    }


def _effective(embedded, annotations):
    result = {}
    for field in ("bpm", "key"):
        value = annotations.get(field)
        result[field] = (
            {
                **value,
                "known": True,
                "accuracy": "operator_verified" if value["verified"] else "unverified",
            }
            if value
            else embedded[field]
        )
    result.update(
        genres=annotations.get("genres")
        if annotations.get("genres") is not None
        else embedded["genres"]["values"],
        tags=annotations.get("tags") or [],
        notes=annotations.get("notes"),
        set_role=annotations.get("set_role"),
        energy=annotations.get("energy"),
    )
    return result


def inspect_metadata(app, recording_id: str, asset_revision_id: str) -> dict:
    """Read hash-matching allowed bytes; absence and ambiguity remain explicitly unknown."""
    with app.db.transaction() as session:
        recording, revision = _catalog(session, recording_id, asset_revision_id)
        labels = {k: getattr(recording, k) for k in ("artist", "title", "version")}
        digest = revision.sha256
        paths = list(
            session.scalars(
                select(FileLocation.path)
                .where(FileLocation.revision_id == asset_revision_id)
                .order_by(FileLocation.managed.desc(), FileLocation.path)
            )
        )
        annotation = _annotation(session.get(RecordingAnnotation, asset_revision_id))
    failure = AppError("FILE_UNAVAILABLE", "No catalog file location is available.")
    for candidate in paths:
        try:
            path = app.workspace.authorize(candidate)
            before = path.stat()
            if before.st_size > 2 * 1024 * 1024 * 1024:
                raise AppError("AUDIO_SIZE_LIMIT", "Metadata inspection is limited to 2 GiB.")
            if checksum(path) != digest:
                raise AppError("RECONCILIATION_REQUIRED", "Catalog bytes changed; reconcile first.")
            embedded = _embedded(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise AppError("FILE_CHANGED", "The file changed during metadata inspection.")
            return {
                "recording_id": recording_id,
                "asset_revision_id": asset_revision_id,
                **labels,
                **annotation,
                "embedded": embedded,
                "effective": _effective(embedded, annotation["annotations"]),
                "provenance": {"path": str(path), "sha256": digest, "read_at": timestamp()},
                "acoustic_analysis_performed": False,
                "source_modified": False,
            }
        except AppError as exc:
            failure = exc
        except OSError:
            failure = AppError("FILE_UNAVAILABLE", "The catalog file became unavailable.")
    raise failure


def _intent(operation, request):
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    if isinstance(request, AnnotationRequest):
        # Omission preserves a field, while an explicit null clears it.
        payload = {key: value for key, value in payload.items() if key in request.model_fields_set}
    payload = {"operation": operation, **payload}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload, digest


def _previous(session, key, digest):
    prior = session.get(Submission, key)
    if prior and prior.request_hash != digest:
        raise AppError("IDEMPOTENCY_CONFLICT", "This key already names a different request.", 409)
    return prior.job_id if prior else None


def _completed(session, request, payload, digest, result, items=()):
    job = Job(
        id=new_id("job"),
        kind="organization",
        state="completed",
        outcome="complete",
        request=payload,
        result=result,
    )
    session.add(job)
    session.flush()
    session.add(Submission(key=request.idempotency_key, request_hash=digest, job_id=job.id))
    for position, (track, state, outcome) in enumerate(items):
        session.add(
            JobItem(
                id=new_id("item"),
                job_id=job.id,
                position=position,
                state=state,
                request={"track": track},
                result=outcome,
            )
        )
    add_event(session, job, "completed", {"operation": payload["operation"]})
    return job.id


def annotate_recording(app, request: AnnotationRequest) -> dict:
    payload, digest = _intent("annotate", request)
    with app.db.transaction() as session:
        prior = _previous(session, request.idempotency_key, digest)
    if prior:
        return app.job(prior)
    patch = {key: value for key, value in payload.items() if key in ANNOTATION_FIELDS}
    if any(
        patch.get(field, {}) and patch[field]["source"] == "native_tag" for field in ("bpm", "key")
    ):
        embedded = inspect_metadata(app, request.recording_id, request.asset_revision_id)[
            "embedded"
        ]
        for field in ("bpm", "key"):
            value = patch.get(field)
            if (
                value
                and value["source"] == "native_tag"
                and value["value"] != embedded[field]["value"]
            ):
                raise AppError(
                    "NATIVE_TAG_MISMATCH", "The supplied value does not match a known embedded tag."
                )
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prior = _previous(session, request.idempotency_key, digest)
        if prior:
            job_id = prior
        else:
            _catalog(session, request.recording_id, request.asset_revision_id)
            row = session.get(RecordingAnnotation, request.asset_revision_id)
            if (row.revision if row else 0) != request.revision:
                raise AppError(
                    "ANNOTATION_STALE", "Read the current annotation revision first.", 409
                )
            annotations = {**(row.annotations if row else {}), **patch}
            now = timestamp()
            if row:
                changed = session.execute(
                    update(RecordingAnnotation)
                    .where(
                        RecordingAnnotation.asset_revision_id == request.asset_revision_id,
                        RecordingAnnotation.revision == request.revision,
                    )
                    .values(annotations=annotations, revision=request.revision + 1, updated_at=now)
                )
                if changed.rowcount != 1:
                    raise AppError("ANNOTATION_STALE", "The annotation changed concurrently.", 409)
            else:
                session.add(
                    RecordingAnnotation(
                        asset_revision_id=request.asset_revision_id,
                        recording_id=request.recording_id,
                        revision=1,
                        annotations=annotations,
                        created_at=now,
                        updated_at=now,
                    )
                )
            result = {
                "recording_id": request.recording_id,
                "asset_revision_id": request.asset_revision_id,
                "revision": request.revision + 1,
                "annotations": annotations,
                "source_modified": False,
            }
            job_id = _completed(session, request, payload, digest, result)
    return app.job(job_id)


def _filter(metadata, request):
    data, filters, reasons, unknowns = metadata["effective"], request.filters, [], []
    for field, active in (
        ("bpm", filters.bpm_min is not None or filters.bpm_max is not None),
        ("key", bool(filters.keys)),
    ):
        if not active:
            continue
        value = data[field]
        if not value["known"] or (filters.require_verified and not value["verified"]):
            unknowns.append(f"{field}_unknown" if not value["known"] else f"{field}_unverified")
        elif field == "bpm":
            if (filters.bpm_min is not None and value["value"] < filters.bpm_min) or (
                filters.bpm_max is not None and value["value"] > filters.bpm_max
            ):
                reasons.append("bpm_outside_range")
        elif value["value"].casefold() not in {k.casefold() for k in filters.keys}:
            reasons.append("key_not_matched")
    for field, wanted in (
        ("genres", filters.genres),
        ("tags", filters.tags),
        ("set_role", filters.set_roles),
    ):
        if not wanted:
            continue
        values = [data[field]] if field == "set_role" and data[field] else data[field]
        if not values:
            unknowns.append(f"{field}_unknown")
        elif not {v.casefold() for v in values}.intersection(v.casefold() for v in wanted):
            reasons.append(f"{field}_not_matched")
    if unknowns and request.unknown == "error":
        raise AppError(
            "ORGANIZATION_UNKNOWN", "A requested filter has unknown or unverified evidence."
        )
    if request.unknown == "exclude":
        reasons.extend(unknowns)
    return reasons, unknowns


def organize_collection(app, request: OrganizationRequest) -> dict:
    """Freeze evaluated membership; a repeated key returns the same completed collection."""
    payload, digest = _intent("collection", request)
    with app.db.transaction() as session:
        prior = _previous(session, request.idempotency_key, digest)
    if prior:
        return app.job(prior)
    seen, evaluated, selected, excluded = {}, [], [], []
    for position, track in enumerate(request.tracks):
        ref = track.model_dump()
        if track.recording_id in seen:
            if seen[track.recording_id] != track.asset_revision_id:
                raise AppError(
                    "ORGANIZATION_REVISION_CONFLICT",
                    "One recording has different selected byte revisions.",
                )
            excluded.append({**ref, "reasons": ["duplicate_reference"]})
            continue
        seen[track.recording_id] = track.asset_revision_id
        try:
            metadata = inspect_metadata(app, track.recording_id, track.asset_revision_id)
        except AppError as exc:
            excluded.append({**ref, "reasons": [exc.code]})
            continue
        evaluated.append(metadata)
        reasons, unknowns = _filter(metadata, request)
        if reasons:
            excluded.append({**ref, "reasons": reasons})
        else:
            selected.append({**metadata, "input_position": position, "unknowns": unknowns})
    if request.order_by != "input":

        def sort_value(track):
            field = request.order_by
            value = track.get(field) if field in {"artist", "title"} else track["effective"][field]
            if field in {"bpm", "key"}:
                value = value["value"]
            if field == "key" and value is not None:
                wheel = re.fullmatch(r"(\d{1,2})([ABdm])", value)
                # Numeric wheel order, without guessing equivalences across key notations.
                return (0, int(wheel[1]), wheel[2]) if wheel else (1, 0, value.casefold())
            return value.casefold() if isinstance(value, str) else value

        # Unknown sort values remain last in either direction; IDs break ties deterministically.
        known = [t for t in selected if sort_value(t) is not None]
        unknown = [t for t in selected if sort_value(t) is None]
        known.sort(
            key=lambda t: (sort_value(t), t["recording_id"], t["asset_revision_id"]),
            reverse=request.descending,
        )
        selected = known + sorted(
            unknown, key=lambda t: (t["recording_id"], t["asset_revision_id"])
        )
    elif request.descending:
        selected.reverse()
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prior = _previous(session, request.idempotency_key, digest)
        if prior:
            job_id = prior
        else:
            # Decisions cannot commit against annotations changed during tag reads.
            for metadata in evaluated:
                row = session.get(RecordingAnnotation, metadata["asset_revision_id"])
                if (row.revision if row else 0) != metadata["revision"]:
                    raise AppError(
                        "ANNOTATION_STALE", "Annotations changed; rebuild the selection.", 409
                    )
            collection = Collection(id=new_id("collection"), name=request.name)
            session.add(collection)
            session.flush()
            items = []
            for position, track in enumerate(selected):
                session.add(
                    Membership(
                        id=new_id("membership"),
                        collection_id=collection.id,
                        recording_id=track["recording_id"],
                        revision_id=track["asset_revision_id"],
                        position=position,
                    )
                )
                items.append(
                    (
                        track,
                        "succeeded",
                        {"collection_position": position, "unknowns": track["unknowns"]},
                    )
                )
            items.extend(
                (track, "skipped", {"exclusion_reasons": track["reasons"]}) for track in excluded
            )
            result = {
                "collection_id": collection.id,
                "name": request.name,
                "selected_count": len(selected),
                "excluded_count": len(excluded),
                "exclusion_counts": dict(
                    Counter(reason for item in excluded for reason in item["reasons"])
                ),
                "excluded": excluded,
                "order_by": request.order_by,
                "unknown_included_count": sum(bool(t["unknowns"]) for t in selected),
                "source_modified": False,
                "acoustic_analysis_performed": False,
                "app_state": "not_imported",
                "device_state": "not_exported",
            }
            job_id = _completed(session, request, payload, digest, result, items)
    return app.job(job_id)
