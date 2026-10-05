"""Durable working-copy verification; native evidence is committed only after all checks."""

import asyncio
import hashlib
import json

from sqlalchemy import select

from djlib.application import delivery
from djlib.application.service import add_event, new_id, require
from djlib.domain.contracts import DeliveryObservation
from djlib.domain.errors import AppError
from djlib.persistence.models import Delivery, Job, JobItem, Submission, timestamp


def _submit(app, delivery_id, revision, operation, observation=None):
    intent = {
        "delivery_id": delivery_id,
        "revision": revision,
        "operation": operation,
        "observation": observation,
    }
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    key = f"delivery-check:{digest}"
    # Replays are resolved before a revision check: a completed job has advanced evidence.
    with app.db.transaction() as session:
        prior = session.get(Submission, key)
        prior_id = prior.job_id if prior else None
    if prior_id:
        return app.job(prior_id)
    d = delivery._load(app, delivery_id)
    delivery._revision(d, revision)
    manifest = delivery._prepared(app, d)
    tracks = manifest["tracks"]
    exact_check = operation == "verify-app" or (
        observation is not None and observation["stage"] == "native_exported"
    )
    if exact_check:
        if operation == "verify-app" and not delivery._app_only(d):
            raise AppError("WORKFLOW_SCOPE", "Use device verification for this workflow.")
        analysis = d["evidence"].get("analyzed", {})
        if analysis.get("outcome") != "passed":
            raise AppError("ANALYSIS_REQUIRED", "Observe native import and analysis first.")
        hashes = {t["recording_id"]: t["sha256"] for t in analysis["assets"]}
        if set(hashes) != {t["recording_id"] for t in tracks}:
            raise AppError("COVERAGE_MISMATCH", "Analysis evidence does not cover every track.")
        tracks = [{**t, "sha256": hashes[t["recording_id"]]} for t in tracks]
    if operation == "observe":
        stage = observation["stage"]
        if stage == "native_exported" and delivery._app_only(d):
            raise AppError("WORKFLOW_SCOPE", "This delivery tracks native app import only.")
        if stage == "native_exported":
            bound = d["evidence"].get("device")
            if not bound:
                raise AppError("DEVICE_REQUIRED", "Bind the intended mounted USB first.")
            if not delivery._same_volume(bound, delivery.inspect_device(bound["path"])):
                raise AppError("DEVICE_CHANGED", "The bound USB is missing or has changed.")
        preceding = "analyzed" if stage == "native_exported" else "imported"
        if d["evidence"].get(preceding, {}).get("outcome") != "passed":
            raise AppError("STAGE_REQUIRED", "Observe the preceding native stage first.")
        if observation["app_version"] != d["request"]["app_version"]:
            raise AppError("APP_VERSION_CHANGED", "Use the delivery's actual app version.")
        if observation["method"] != "native_app_ui":
            raise AppError("EVIDENCE_METHOD", "This stage needs a native app observation.")
        if (
            observation["track_count"] != manifest["unique_track_count"]
            or observation["playlist_counts"] != manifest["playlist_counts"]
            or set(observation["checked_recording_ids"]) != {t["recording_id"] for t in tracks}
        ):
            raise AppError("COVERAGE_MISMATCH", "Observed coverage must match the frozen manifest.")
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prior = session.get(Submission, key)
        if prior:
            job_id = prior.job_id
        else:
            if require(session, Delivery, delivery_id).revision != revision:
                raise AppError(
                    "DELIVERY_STALE", "Delivery changed; read its current revision.", 409
                )
            job = Job(id=new_id("job"), kind="delivery_check", request=intent)
            session.add(job)
            session.flush()
            session.add(Submission(key=key, request_hash=digest, job_id=job.id))
            for position, track in enumerate(tracks):
                session.add(
                    JobItem(
                        id=new_id("item"),
                        job_id=job.id,
                        position=position,
                        request={"track": track},
                    )
                )
            add_event(session, job, "accepted", {"operation": operation})
            job_id = job.id
    return app.job(job_id)


def submit_observation(app, delivery_id, observation: DeliveryObservation):
    if observation.stage not in {"analyzed", "native_exported"} or observation.outcome != "passed":
        return delivery.observe_delivery(app, delivery_id, observation)
    return _submit(
        app, delivery_id, observation.revision, "observe", observation.model_dump(mode="json")
    )


def submit_app_verification(app, delivery_id, revision):
    return _submit(app, delivery_id, revision, "verify-app")


async def prepare_delivery_check_item(app, job_id, generation, item_id):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        item = require(session, JobItem, item_id)
        item.state = "running"
        track, operation = item.request["track"], job.request["operation"]
        observation = job.request.get("observation")
    try:
        checked = await asyncio.to_thread(delivery._reconcile_analysis_track, app, track)
        exact_check = operation == "verify-app" or (
            observation is not None and observation["stage"] == "native_exported"
        )
        if exact_check and checked["sha256"] != track["sha256"]:
            raise AppError("ANALYSIS_CHANGED", "Working copies changed; record analysis again.")
        state, result = "succeeded", {"checked": checked}
    except (AppError, OSError) as exc:
        error = (
            exc
            if isinstance(exc, AppError)
            else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
        )
        state, result = "failed", {"error": error.as_dict()}
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        item = require(session, JobItem, item_id)
        item.state, item.result = state, result
        add_event(session, job, "working_file_checked", {"item_id": item_id, "state": state})


def _fail_job(app, job_id, generation, error, *, payload=None):
    """Fence both the job outcome and any revocation of its pinned delivery evidence."""
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        result = {"error": error}
        if payload is not None:
            row = require(session, Delivery, payload["delivery_id"])
            if row.revision == payload["revision"]:
                evidence = dict(row.evidence)
                for stage in delivery.STAGES[2:]:
                    evidence.pop(stage, None)
                evidence.pop("readback", None)
                evidence["app_readback"] = {
                    "status": "failed",
                    "error": error,
                    "checked_at": timestamp(),
                }
                evidence["analyzed"] = {
                    **evidence.get("analyzed", {}),
                    "outcome": "failed",
                    "error": error,
                }
                row.evidence, row.revision, row.updated_at = evidence, row.revision + 1, timestamp()
                result.update(delivery_id=row.id, revision=row.revision, evidence_committed=True)
            else:
                result = {
                    "error": AppError(
                        "DELIVERY_STALE", "Delivery changed; read its current revision.", 409
                    ).as_dict()
                }
        job.state, job.outcome, job.result = "failed", None, result
        add_event(session, job, "failed")


def finish_delivery_check(app, job_id, generation):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        payload = dict(job.request)
        items = list(
            session.scalars(
                select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.position)
            )
        )
        if any(i.state in {"pending", "running"} for i in items):
            return
        error = next((i.result.get("error") for i in items if i.state == "failed"), None)
        reconciled = [i.result["checked"] for i in items if i.state == "succeeded"]
    if error:
        _fail_job(app, job_id, generation, error, payload=payload)
        return
    try:
        # Evidence CAS and job completion occur in the SAME transaction, fenced by
        # generation. A late pause/cancel can never commit native-stage evidence.
        if payload["operation"] == "observe":
            delivery.observe_delivery(
                app,
                payload["delivery_id"],
                DeliveryObservation(**payload["observation"]),
                reconciled=reconciled,
                job_guard=(job_id, generation),
            )
        else:
            delivery.verify_app(
                app,
                payload["delivery_id"],
                payload["revision"],
                reconciled=reconciled,
                job_guard=(job_id, generation),
            )
    except (AppError, OSError) as exc:
        error = (
            exc
            if isinstance(exc, AppError)
            else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
        )
        if error.code == "JOB_STALE":
            return
        file_failures = {
            "AUDIO_CHANGED",
            "ANALYSIS_CHANGED",
            "FILE_CHANGED",
            "FILE_UNAVAILABLE",
            "SOURCE_NOT_ALLOWED",
            "AUDIO_VERIFY_FAILED",
            "RECONCILIATION_STALE",
        }
        _fail_job(
            app,
            job_id,
            generation,
            error.as_dict(),
            payload=payload if error.code in file_failures else None,
        )
