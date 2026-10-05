"""Accept organization before expensive reads; checkpoint each frozen catalog reference."""

import asyncio
from collections import Counter

from sqlalchemy import select

from djlib.application import organization as org
from djlib.application.service import add_event, new_id, require
from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import OrganizationRequest
from djlib.persistence.models import Collection, Job, JobItem, Membership, Submission
from djlib.persistence.organization_models import RecordingAnnotation


def submit_organization(app, request: OrganizationRequest) -> dict:
    payload, digest = org._intent("collection", request)
    seen = {}
    for track in request.tracks:
        previous = seen.setdefault(track.recording_id, track.asset_revision_id)
        if previous != track.asset_revision_id:
            raise AppError(
                "ORGANIZATION_REVISION_CONFLICT",
                "One recording has different selected byte revisions.",
            )
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prior = org._previous(session, request.idempotency_key, digest)
        if prior:
            job_id = prior
        else:
            job = Job(id=new_id("job"), kind="organize", request=payload)
            session.add(job)
            session.flush()
            session.add(Submission(key=request.idempotency_key, request_hash=digest, job_id=job.id))
            seen = set()
            for position, track in enumerate(request.tracks):
                row = session.get(RecordingAnnotation, track.asset_revision_id)
                duplicate = track.recording_id in seen
                seen.add(track.recording_id)
                session.add(
                    JobItem(
                        id=new_id("item"),
                        job_id=job.id,
                        position=position,
                        request={
                            "track": track.model_dump(),
                            "annotation_revision": row.revision if row else 0,
                        },
                        state="skipped" if duplicate else "pending",
                        result={"exclusion_reasons": ["duplicate_reference"]} if duplicate else {},
                    )
                )
            add_event(session, job, "accepted", {"operation": "organization"})
            job_id = job.id
    return app.job(job_id)


async def prepare_organization_item(app, job_id, generation, item_id):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        item = require(session, JobItem, item_id)
        item.state = "running"
        ref, expected = item.request["track"], item.request["annotation_revision"]
        body = OrganizationRequest(
            **{k: v for k, v in job.request.items() if k != "operation"}, idempotency_key="worker"
        )
    try:
        metadata = await asyncio.to_thread(org.inspect_metadata, app, **ref)
        if metadata["revision"] != expected:
            raise AppError("ANNOTATION_STALE", "Annotations changed; rebuild the selection.", 409)
        reasons, unknowns = org._filter(metadata, body)
        state = "skipped" if reasons else "succeeded"
        result = {"metadata": metadata, "exclusion_reasons": reasons, "unknowns": unknowns}
    except AppError as error:
        state, result = "failed", {"error": error.as_dict(), "exclusion_reasons": [error.code]}
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        item = require(session, JobItem, item_id)
        item.state, item.result = state, result
        add_event(session, job, "organization_item_checked", {"item_id": item_id, "state": state})


def _ordered(selected, request):
    if request.order_by == "input":
        return list(reversed(selected)) if request.descending else selected
    import re

    def value(track):
        field = request.order_by
        raw = track.get(field) if field in {"artist", "title"} else track["effective"][field]
        if field in {"bpm", "key"}:
            raw = raw["value"]
        if field == "key" and raw is not None:
            wheel = re.fullmatch(r"(\d{1,2})([ABdm])", raw)
            return (0, int(wheel[1]), wheel[2]) if wheel else (1, 0, raw.casefold())
        return raw.casefold() if isinstance(raw, str) else raw

    known = [t for t in selected if value(t) is not None]
    unknown = [t for t in selected if value(t) is None]
    known.sort(
        key=lambda t: (value(t), t["recording_id"], t["asset_revision_id"]),
        reverse=request.descending,
    )
    return known + sorted(unknown, key=lambda t: (t["recording_id"], t["asset_revision_id"]))


def finish_organization(app, job_id, generation):
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        items = list(
            session.scalars(
                select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.position)
            )
        )
        if any(i.state in {"pending", "running"} for i in items):
            return
        request = OrganizationRequest(
            **{k: v for k, v in job.request.items() if k != "operation"}, idempotency_key="worker"
        )
        selected, excluded = [], []
        for item in items:
            metadata = item.result.get("metadata")
            if metadata:
                row = session.get(RecordingAnnotation, metadata["asset_revision_id"])
                if (row.revision if row else 0) != metadata["revision"]:
                    job.state, job.outcome = "failed", None
                    job.result = {
                        "error": AppError(
                            "ANNOTATION_STALE", "Annotations changed; rebuild the selection.", 409
                        ).as_dict()
                    }
                    add_event(session, job, "failed")
                    return
            if item.state == "succeeded":
                selected.append({**metadata, "unknowns": item.result["unknowns"]})
            else:
                excluded.append(
                    {
                        **item.request["track"],
                        "reasons": item.result.get("exclusion_reasons", ["unchecked"]),
                    }
                )
        # Unknown='error' and stale annotations are whole-intent failures, not partial collections.
        blocking = next(
            (
                i.result.get("error")
                for i in items
                if i.result.get("error", {}).get("code")
                in {"ORGANIZATION_UNKNOWN", "ANNOTATION_STALE"}
            ),
            None,
        )
        if blocking:
            job.state, job.outcome, job.result = "failed", None, {"error": blocking}
            add_event(session, job, "failed")
            return
        ordered = _ordered(selected, request)
        collection = Collection(id=new_id("collection"), name=request.name)
        session.add(collection)
        session.flush()
        for position, track in enumerate(ordered):
            session.add(
                Membership(
                    id=new_id("membership"),
                    collection_id=collection.id,
                    recording_id=track["recording_id"],
                    revision_id=track["asset_revision_id"],
                    position=position,
                )
            )
        job.result = {
            "collection_id": collection.id,
            "name": request.name,
            "selected_count": len(ordered),
            "excluded_count": len(excluded),
            "exclusion_counts": dict(Counter(r for t in excluded for r in t["reasons"])),
            "excluded": excluded,
            "order_by": request.order_by,
            "unknown_included_count": sum(bool(t["unknowns"]) for t in selected),
            "source_modified": False,
            "acoustic_analysis_performed": False,
            "app_state": "not_tracked_here",
            "device_state": "not_tracked_here",
        }
        job.state = "completed"
        job.outcome = (
            "completed_with_gaps" if any(i.state == "failed" for i in items) else "complete"
        )
        add_event(session, job, "completed", {"operation": "organization"})
