"""Catalog use cases and transactional state transitions owned by the coordinator."""

import hashlib
import json
from collections import Counter
from uuid import uuid4

from sqlalchemy import select

from djlib import __version__
from djlib.domain.contracts import CollectionRequest, DownloadRequest, Profile, StartRequest
from djlib.domain.errors import AppError
from djlib.persistence.database import Database
from djlib.persistence.models import (
    AssetRevision,
    Collection,
    Delivery,
    Event,
    FileLocation,
    Job,
    JobItem,
    Membership,
    Plan,
    Recording,
    Review,
    Submission,
    timestamp,
)
from djlib.sources.web import validate_url
from djlib.workspace import Workspace

ITEM_STATES = frozenset(
    {"pending", "running", "succeeded", "failed", "skipped", "needs_input", "cancelled"}
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


ENTITY_NAMES = {
    "AssetRevision": "Asset revision",
    "JobItem": "Job item",
    "RequestLedger": "Request list",
}


def require(session, model, entity_id: str):
    value = session.get(model, entity_id)
    if value is None:
        name = ENTITY_NAMES.get(model.__name__, model.__name__)
        raise AppError("NOT_FOUND", f"{name} was not found.", 404)
    return value


def add_event(session, job: Job, kind: str, data: dict | None = None) -> None:
    job.updated_at = timestamp()
    session.add(Event(job_id=job.id, kind=kind, data=data or {}))


class Application:
    """Short synchronous transactions; the async worker performs expensive I/O separately."""

    def __init__(self, workspace: Workspace, database: Database):
        self.workspace, self.db = workspace, database

    def capabilities(self) -> dict:
        return {
            "application_version": __version__,
            "schema_version": "1",
            "workspace_id": self.workspace.config().workspace_id,
            "implemented": [
                "local_audio_index",
                "local_tracklist_collection",
                "durable_jobs",
                "review_queue",
                "manifest_export",
                "rekordbox_xml_handoff",
                "usb_space_preflight",
                "selected_web_audio_download",
                "set_metadata_inspection",
                "json_cli",
                "mcp_stdio",
                "agent_session_setup",
                "targeted_delivery_workflow",
                "native_app_working_copies",
                "device_audio_readback",
                "operator_native_stage_evidence",
                "app_import_workflow",
                "owned_request_matching",
                "missing_track_ledger",
                "catalog_annotations",
                "organization_filters",
                "native_rekordbox_snapshot_inspection",
                "catalog_pagination",
                "saved_work_discovery",
                "explicit_root_management",
                "file_reconciliation",
            ],
            "planned": [
                "soulseek",
                "online_source_discovery",
                "set_recognition",
                "artist_catalogs",
                "native_serato",
                "native_rekordbox",
                "device_export",
                "standalone_chat",
            ],
            "identity_method": "supplied labels and embedded tags; no acoustic identification yet",
            "native_automation_available": False,
            "delivery_default": (
                "small_pilot_before_bulk; native app operations require "
                "an operator or host UI tools"
            ),
            "stage": "experimental alpha; consult docs/STATUS.md for app/provider test evidence",
        }

    def profile(self, name: str) -> Profile:
        profile = self.workspace.config().profiles.get(name)
        if profile is None:
            raise AppError(
                "PROFILE_NOT_FOUND", "The requested preference profile does not exist.", 404
            )
        return profile

    def plan(self, request: CollectionRequest) -> dict:
        profile = self.profile(request.profile)
        if len(request.tracks) > profile.max_items:
            raise AppError("ITEM_LIMIT", "The collection exceeds this profile's item limit.")
        for track in request.tracks:
            self.workspace.authorize(track.path)
        with self.db.transaction() as session:
            value = Plan(
                id=new_id("plan"),
                request=request.model_dump(mode="json"),
                profile=profile.model_dump(mode="json"),
            )
            session.add(value)
            session.flush()
            result = self._plan(value)
        return result

    @staticmethod
    def _plan(value: Plan) -> dict:
        return {
            "plan_id": value.id,
            "revision": value.revision,
            "request": {
                "name": value.request["name"],
                "profile": value.request["profile"],
                "track_count": len(value.request["tracks"]),
                "track_preview": value.request["tracks"][:10],
            },
            "effective_profile": value.profile,
            "effects": ["inspect local audio", "add catalog/collection references"]
            + (
                ["copy accepted audio into managed storage"]
                if value.profile["copy_into_library"]
                else []
            ),
            "identity_evidence": "supplied labels checked against embedded metadata",
            "created_at": value.created_at,
        }

    def get_plan(self, plan_id: str) -> dict:
        with self.db.transaction() as session:
            return self._plan(require(session, Plan, plan_id))

    def submit(self, kind: str, payload: dict, key: str) -> dict:
        semantic_hash = hashlib.sha256(
            json.dumps({"kind": kind, "payload": payload}, sort_keys=True).encode()
        ).hexdigest()
        with self.db.transaction() as session:
            if kind == "delivery":
                # Acquire the SQLite writer reservation before reading the binding.
                # Concurrent clients cannot both observe an unprepared delivery.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            previous = session.get(Submission, key)
            if previous:
                if previous.request_hash != semantic_hash:
                    raise AppError(
                        "IDEMPOTENCY_CONFLICT", "This key already names a different request.", 409
                    )
                job_id = previous.job_id
            elif kind == "delivery" and require(session, Delivery, payload["delivery_id"]).job_id:
                job_id = require(session, Delivery, payload["delivery_id"]).job_id
                if require(session, Job, job_id).request != payload:
                    raise AppError(
                        "DELIVERY_CONFLICT",
                        "This delivery already has a different preparation.",
                        409,
                    )
                session.add(Submission(key=key, request_hash=semantic_hash, job_id=job_id))
            else:
                value = Job(id=new_id("job"), kind=kind, request=payload)
                session.add(value)
                session.flush()
                if kind in {"collection", "download"}:
                    collection = Collection(id=new_id("collection"), name=payload["name"])
                    session.add(collection)
                    value.result = {"collection_id": collection.id}
                    for position, track in enumerate(payload["tracks"]):
                        session.add(
                            JobItem(
                                id=new_id("item"),
                                job_id=value.id,
                                position=position,
                                request={
                                    "track" if kind == "collection" else "source": track,
                                    "identity_decision": None,
                                },
                            )
                        )
                elif kind == "organize":
                    for position, track in enumerate(payload["tracks"]):
                        session.add(
                            JobItem(
                                id=new_id("item"),
                                job_id=value.id,
                                position=position,
                                request={"track": track},
                            )
                        )
                elif kind == "delivery":
                    require(session, Delivery, payload["delivery_id"]).job_id = value.id
                    value.result = {"delivery_id": payload["delivery_id"]}
                    for position, track in enumerate(payload["tracks"]):
                        session.add(
                            JobItem(
                                id=new_id("item"),
                                job_id=value.id,
                                position=position,
                                request={"track": track},
                            )
                        )
                session.add(Submission(key=key, request_hash=semantic_hash, job_id=value.id))
                add_event(session, value, "accepted")
                job_id = value.id
        return self.job(job_id)

    def start(self, request: StartRequest) -> dict:
        with self.db.transaction() as session:
            plan = require(session, Plan, request.plan_id)
            if plan.revision != request.revision:
                raise AppError("PLAN_STALE", "The plan revision has changed.", 409)
            payload = {**plan.request, "effective_profile": plan.profile, "plan_id": plan.id}
        # Revalidate before committing the job; metadata changes are checked again by the worker.
        for track in payload["tracks"]:
            self.workspace.authorize(track["path"])
        return self.submit("collection", payload, request.idempotency_key)

    def scan(self, path: str, key: str) -> dict:
        root = self.workspace.authorize(path, directory=True)
        if root == self.workspace.root:
            raise AppError("SCAN_SCOPE", "Scan a media subdirectory, not the whole workspace.")
        return self.submit("scan", {"path": str(root)}, key)

    def download(self, request: DownloadRequest) -> dict:
        for track in request.tracks:
            validate_url(track.url)
        payload = request.model_dump(mode="json", exclude={"idempotency_key"})
        payload["effective_profile"] = self.profile("archive").model_dump(mode="json")
        return self.submit("download", payload, request.idempotency_key)

    def job(self, job_id: str) -> dict:
        with self.db.transaction() as session:
            value = require(session, Job, job_id)
            counts = Counter(
                session.scalars(select(JobItem.state).where(JobItem.job_id == value.id))
            )
            submission = session.scalar(select(Submission).where(Submission.job_id == value.id))
            return {
                "job_id": value.id,
                "kind": value.kind,
                "state": value.state,
                "outcome": value.outcome,
                "counts": dict(counts),
                "result": value.result,
                "idempotency_key": submission.key if submission else None,
                "created_at": value.created_at,
                "updated_at": value.updated_at,
                "next_poll_after_seconds": 2 if value.state in {"queued", "running"} else None,
            }

    def jobs(self, limit: int = 20, query: str = "", after: str | None = None) -> dict:
        from djlib.application.catalog_paging import saved

        page = saved(self, "jobs", query, limit, after)
        page["jobs"] = [{**row, **self.job(row["job_id"])} for row in page["jobs"]]
        return page

    def saved(self, kind: str, query: str = "", limit: int = 20, after: str | None = None) -> dict:
        from djlib.application.catalog_paging import saved

        return saved(self, kind, query, limit, after)

    def items(
        self, job_id: str, limit: int = 20, after: int = -1, state: str | None = None
    ) -> dict:
        if state is not None and state not in ITEM_STATES:
            raise AppError(
                "INPUT_INVALID", "Use an item state: " + ", ".join(sorted(ITEM_STATES)) + "."
            )
        with self.db.transaction() as session:
            require(session, Job, job_id)
            query = select(JobItem).where(JobItem.job_id == job_id, JobItem.position > after)
            if state:
                query = query.where(JobItem.state == state)
            rows = list(session.scalars(query.order_by(JobItem.position).limit(limit + 1)))
            return {
                "items": [
                    {
                        "item_id": row.id,
                        "position": row.position,
                        "state": row.state,
                        "input": row.request.get("track") or row.request.get("source"),
                        "result": row.result,
                    }
                    for row in rows[:limit]
                ],
                "next_cursor": rows[limit - 1].position if len(rows) > limit else None,
            }

    def events(self, job_id: str, after: int = 0, limit: int = 50) -> dict:
        with self.db.transaction() as session:
            require(session, Job, job_id)
            rows = list(
                session.scalars(
                    select(Event)
                    .where(Event.job_id == job_id, Event.id > after)
                    .order_by(Event.id)
                    .limit(limit + 1)
                )
            )
            return {
                "events": [
                    {"cursor": r.id, "kind": r.kind, "data": r.data, "created_at": r.created_at}
                    for r in rows[:limit]
                ],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    def control(self, job_id: str, action: str) -> dict:
        with self.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.kind == "organization" or (job.kind == "organize" and job.state == "completed"):
                raise AppError(
                    "JOB_TERMINAL",
                    "This organization transaction is complete. Submit a new explicit "
                    "mutation instead of requeuing it.",
                    409,
                )
            if job.kind == "delivery_check" and (
                job.state == "completed" or (job.result or {}).get("evidence_committed")
            ):
                raise AppError(
                    "JOB_TERMINAL",
                    "This check committed delivery evidence. Read the current delivery revision "
                    "and submit a new check instead of requeuing it.",
                    409,
                )
            if job.state == "cancelled" and action != "cancel":
                raise AppError(
                    "JOB_TERMINAL", "A cancelled job cannot resume; create a new intent.", 409
                )
            if job.state == "completed" and action != "retry":
                raise AppError("JOB_TERMINAL", "This job is already complete.", 409)
            if action == "pause" and job.state in {"queued", "running"}:
                job.state = "paused"
            elif action == "cancel":
                job.state = "cancelled"
            elif action in {"resume", "retry"}:
                if job.state == "completed" and action == "resume":
                    return self._control_noop(job.id)
                job.state, job.outcome = "queued", None
            job.generation += 1
            for item in session.scalars(select(JobItem).where(JobItem.job_id == job.id)):
                if action == "cancel" and item.state not in {"succeeded", "skipped"}:
                    item.state = "cancelled"
                    for review in session.scalars(
                        select(Review).where(Review.item_id == item.id, Review.state == "open")
                    ):
                        review.state = "cancelled"
                elif item.state == "running" or (action == "retry" and item.state == "failed"):
                    item.state = "pending"
            add_event(session, job, action)
        return self.job(job_id)

    def _control_noop(self, job_id: str) -> dict:
        return self.job(job_id)

    def reviews(self, job_id: str | None = None, limit: int = 20) -> dict:
        with self.db.transaction() as session:
            query = select(Review).join(JobItem).where(Review.state == "open")
            if job_id:
                query = query.where(JobItem.job_id == job_id)
            rows = list(session.scalars(query.order_by(Review.id).limit(limit)))
            return {
                "reviews": [
                    {
                        "review_id": r.id,
                        "item_id": r.item_id,
                        "revision": r.revision,
                        "reason": r.reason,
                        "evidence": r.evidence,
                        "choices": ["accept_requested", "use_file_metadata", "skip"],
                    }
                    for r in rows
                ]
            }

    def resolve(self, review_id: str, revision: int, choice: str) -> dict:
        with self.db.transaction() as session:
            review = require(session, Review, review_id)
            if review.state != "open" or review.revision != revision:
                raise AppError("REVIEW_STALE", "This review decision is no longer current.", 409)
            item = require(session, JobItem, review.item_id)
            job = require(session, Job, item.job_id)
            if job.state == "cancelled":
                raise AppError("JOB_TERMINAL", "This review belongs to a cancelled job.", 409)
            request = dict(item.request)
            request["identity_decision"] = choice
            if choice == "use_file_metadata":
                track = dict(request["track"])
                metadata = review.evidence["file_metadata"]
                track.update(
                    artist=metadata["artist"] or track["artist"],
                    title=metadata["title"] or track["title"],
                    version="",
                )
                request["track"] = track
            item.request = request
            item.state = "skipped" if choice == "skip" else "pending"
            review.state, review.revision = "resolved", review.revision + 1
            if job.state not in {"paused", "running"}:
                job.state, job.outcome = "queued", None
            add_event(session, job, "review_resolved", {"review_id": review.id, "choice": choice})
        return self.job(job.id)

    def library(self, query: str = "", limit: int = 20, after: str | None = None) -> dict:
        from djlib.application.catalog_paging import library

        return library(self, query, limit, after)

    @staticmethod
    def _track(
        recording: Recording, revision: AssetRevision, location: FileLocation | None
    ) -> dict:
        return {
            "recording_id": recording.id,
            "asset_revision_id": revision.id,
            "artist": recording.artist,
            "title": recording.title,
            "version": recording.version,
            "sha256": revision.sha256,
            "properties": revision.properties,
            "path": location.path if location else None,
            "managed": location.managed if location else False,
            "location_availability": "not_checked" if location else "no_recorded_location",
            "last_known_path": revision.properties.get("last_known_path")
            or revision.properties.get("indexed_path"),
            "identity_evidence": recording.evidence,
        }

    def collection(self, collection_id: str) -> dict:
        with self.db.transaction() as session:
            value = require(session, Collection, collection_id)
            rows = session.execute(
                select(Recording, AssetRevision, FileLocation)
                .select_from(Membership)
                .join(Recording, Membership.recording_id == Recording.id)
                .join(AssetRevision, Membership.revision_id == AssetRevision.id)
                .outerjoin(FileLocation, FileLocation.revision_id == AssetRevision.id)
                .where(Membership.collection_id == value.id)
                .order_by(Membership.position, FileLocation.managed.desc())
            ).all()
            unique = {}
            for row in rows:
                key = row[0].id
                if key not in unique:
                    unique[key] = self._track(*row)
            return {
                "collection_id": value.id,
                "name": value.name,
                "revision": value.revision,
                "tracks": list(unique.values()),
                "app_state": "not_tracked_here",
                "device_state": "not_tracked_here",
                "native_state_scope": "collection_membership_does_not_track_native_apps_or_devices",
            }

    def export(self, collection_id: str, key: str) -> dict:
        snapshot = self.collection(collection_id)
        if not snapshot["tracks"]:
            raise AppError("COLLECTION_EMPTY", "No validated tracks are ready to export.")
        if any(not track["path"] for track in snapshot["tracks"]):
            raise AppError(
                "FILE_UNAVAILABLE", "A selected historical revision has no current file location."
            )
        return self.submit("export", {"snapshot": snapshot}, key)
