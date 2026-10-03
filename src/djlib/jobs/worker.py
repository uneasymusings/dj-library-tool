"""Bounded background work with generation-fenced commits and an ingestion journal."""

import asyncio
import logging
import os
import shutil
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from djlib.application.service import Application, add_event, new_id, require
from djlib.audio.inspection import SUPPORTED_EXTENSIONS, Inspection, checksum, inspect_audio, labels
from djlib.domain.contracts import Profile, TrackInput, normalize, recording_key, version_markers
from djlib.domain.errors import AppError
from djlib.exporting.handoff import atomic_text, rekordbox_xml
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Collection,
    FileLocation,
    Job,
    JobItem,
    Membership,
    Operation,
    Recording,
    Review,
)
from djlib.sources.web import download
from djlib.workspace import atomic_json

logger = logging.getLogger(__name__)


class Worker:
    """One scheduling authority; slow audio/filesystem work runs outside transactions."""

    def __init__(self, application: Application):
        self.app = application

    def recover(self) -> None:
        """Interrupted attempts become pending; promoted files are reconciled by their hash."""
        with self.app.db.transaction() as session:
            for job in session.scalars(select(Job).where(Job.state == "running")):
                job.state, job.generation = "queued", job.generation + 1
                for item in session.scalars(select(JobItem).where(JobItem.job_id == job.id)):
                    if item.state == "running":
                        item.state = "pending"
                add_event(session, job, "recovered")

    async def run(self) -> None:
        self.recover()
        while True:
            with self.app.db.transaction() as session:
                job = session.scalar(
                    select(Job).where(Job.state == "queued").order_by(Job.created_at)
                )
                if job:
                    job.state, job.generation = "running", job.generation + 1
                    job_id, generation = job.id, job.generation
                    add_event(session, job, "started")
                else:
                    job_id = None
            if job_id is None:
                await asyncio.sleep(0.1)
                continue
            try:
                await self.execute(job_id, generation)
            except asyncio.CancelledError:
                # Service shutdown is a checkpoint, not a cancellation of the user's intent.
                self.recover()
                raise
            except Exception as exc:
                logger.exception("Job execution failed: %s", job_id)
                with self.app.db.transaction() as session:
                    job = require(session, Job, job_id)
                    if job.state == "running" and job.generation == generation:
                        job.state = "failed"
                        error = (
                            exc.as_dict()
                            if isinstance(exc, AppError)
                            else {
                                "code": "JOB_FAILED",
                                "message": "Execution failed; inspect local diagnostics and retry.",
                                "retryable": True,
                            }
                        )
                        job.result = {**job.result, "error": error}
                        add_event(session, job, "failed")

    def active(self, job_id: str, generation: int) -> bool:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            return job.state == "running" and job.generation == generation

    async def execute(self, job_id: str, generation: int) -> None:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            kind, request = job.kind, dict(job.request)
        if kind == "export":
            await self.export(job_id, generation, request["snapshot"])
            return
        if kind == "scan":
            with self.app.db.transaction() as session:
                exists = session.scalar(select(JobItem.id).where(JobItem.job_id == job_id))
            if not exists:
                tracks = await asyncio.to_thread(self.discover, request["path"])
                if not self.active(job_id, generation):
                    return
                with self.app.db.transaction() as session:
                    for position, track in enumerate(tracks):
                        session.add(
                            JobItem(
                                id=new_id("item"),
                                job_id=job_id,
                                position=position,
                                request={"track": track, "identity_decision": None},
                            )
                        )
        with self.app.db.transaction() as session:
            item_ids = list(
                session.scalars(
                    select(JobItem.id)
                    .where(JobItem.job_id == job_id, JobItem.state == "pending")
                    .order_by(JobItem.position)
                )
            )
        for item_id in item_ids:
            if not self.active(job_id, generation):
                return
            await self.ingest(job_id, generation, item_id)
        if self.active(job_id, generation):
            with self.app.db.transaction() as session:
                job = require(session, Job, job_id)
                counts = Counter(
                    session.scalars(select(JobItem.state).where(JobItem.job_id == job_id))
                )
                job.state = "needs_attention" if counts["needs_input"] else "completed"
                job.outcome = (
                    None
                    if counts["needs_input"]
                    else (
                        "completed_with_gaps"
                        if counts["failed"] or counts["skipped"]
                        else "complete"
                    )
                )
                add_event(session, job, job.state, {"counts": dict(counts)})

    def discover(self, value: str) -> list[dict]:
        root = self.app.workspace.authorize(value, directory=True)
        tracks = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            authorized = self.app.workspace.authorize(str(path))
            artist, title = labels(authorized)
            track = TrackInput(
                path=str(authorized), artist=artist or "Unknown artist", title=title or path.stem
            )
            tracks.append(track.model_dump(mode="json"))
            if len(tracks) > 10_000:
                raise AppError(
                    "ITEM_LIMIT", "A scan is limited to 10,000 media files; choose a subfolder."
                )
        return tracks

    async def ingest(self, job_id: str, generation: int, item_id: str) -> None:
        with self.app.db.transaction() as session:
            item = require(session, JobItem, item_id)
            job = require(session, Job, job_id)
            item.state = "running"
            item_request = dict(item.request)
            decision = item.request.get("identity_decision")
            profile = Profile.model_validate(job.request.get("effective_profile", {}))
            collection_id = job.result.get("collection_id")
            operation_id = new_id("op")
            session.add(
                Operation(
                    id=operation_id,
                    item_id=item_id,
                    phase="planned",
                    detail={"source": item_request.get("track", {}).get("path")},
                )
            )
        try:
            if "track" not in item_request:
                source_input = item_request["source"]
                if shutil.disk_usage(self.app.workspace.root).free < 1100 * 1024 * 1024:
                    raise AppError(
                        "DISK_RESERVE_REACHED", "Web acquisition requires 1.1 GiB of free space."
                    )
                task = asyncio.create_task(
                    download(source_input["url"], self.app.workspace.incoming / item_id)
                )
                try:
                    while not task.done():
                        if not self.active(job_id, generation):
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            return
                        await asyncio.sleep(0.2)
                    path, provenance = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                if not self.active(job_id, generation):
                    return
                item_request["track"] = {
                    "path": str(path),
                    **{k: source_input[k] for k in ("artist", "title", "version")},
                }
                item_request["provenance"] = provenance
                with self.app.db.transaction() as session:
                    require(session, JobItem, item_id).request = item_request
            track = TrackInput.model_validate(item_request["track"])
            source = self.app.workspace.authorize(track.path)
            inspection = await asyncio.to_thread(inspect_audio, source)
            if not self.active(job_id, generation):
                return
            if (
                self.identity_conflict(track, inspection, profile.exact_version)
                and decision is None
            ):
                self.review(item_id, job_id, track, inspection)
                return
            location = source
            managed = False
            if profile.copy_into_library:
                required = inspection.size_bytes + profile.disk_reserve_bytes
                if shutil.disk_usage(self.app.workspace.root).free < required:
                    raise AppError("DISK_RESERVE_REACHED", "Insufficient disk space for this copy.")
                staging = self.app.workspace.staging / f"{operation_id}.part"
                await asyncio.to_thread(shutil.copyfile, source, staging)
                copied_hash = await asyncio.to_thread(checksum, staging)
                if copied_hash != inspection.sha256:
                    raise AppError("FILE_CHANGED", "The source changed before copying completed.")
                if not self.active(job_id, generation):
                    return
                location = (
                    self.app.workspace.managed / f"{inspection.sha256}{source.suffix.lower()}"
                )
                with self.app.db.transaction() as session:
                    operation = require(session, Operation, operation_id)
                    operation.phase = "staged"
                    operation.detail = {
                        "source": str(source),
                        "staging": str(staging),
                        "destination": str(location),
                        "sha256": inspection.sha256,
                    }
                if location.exists():
                    if checksum(location) != inspection.sha256:
                        raise AppError(
                            "DESTINATION_CONFLICT", "A managed destination has unexpected contents."
                        )
                    staging.unlink()
                else:
                    os.replace(staging, location)
                    location.chmod(0o600)
                managed = True
            self.accept(
                job_id,
                generation,
                item_id,
                operation_id,
                track,
                inspection,
                location,
                managed,
                collection_id,
                decision,
            )
        except AppError as exc:
            if self.active(job_id, generation):
                with self.app.db.transaction() as session:
                    item = require(session, JobItem, item_id)
                    item.state, item.result = "failed", {"error": exc.as_dict()}
                    require(session, Operation, operation_id).phase = "failed"
                    add_event(
                        session,
                        require(session, Job, job_id),
                        "item_failed",
                        {"item_id": item_id, "code": exc.code},
                    )

    @staticmethod
    def identity_conflict(
        track: TrackInput, inspection: Inspection, exact_version: bool = True
    ) -> bool:
        if inspection.artist and normalize(inspection.artist) != normalize(track.artist):
            return True
        if inspection.title:
            expected = {normalize(track.title), normalize(f"{track.title} {track.version}")}
            if normalize(inspection.title) not in expected:
                return True
            requested = version_markers(f"{track.title} {track.version}")
            if exact_version and requested != version_markers(inspection.title):
                return True
        return False

    def review(self, item_id: str, job_id: str, track: TrackInput, inspection: Inspection) -> None:
        with self.app.db.transaction() as session:
            value = Review(
                id=new_id("review"),
                item_id=item_id,
                reason="METADATA_CONFLICT",
                evidence={
                    "requested": track.model_dump(mode="json"),
                    "file_metadata": {"artist": inspection.artist, "title": inspection.title},
                    "duration_seconds": inspection.duration_seconds,
                },
            )
            session.add(value)
            item = require(session, JobItem, item_id)
            item.state, item.result = "needs_input", {"review_id": value.id}
            add_event(
                session, require(session, Job, job_id), "review_required", {"review_id": value.id}
            )

    def accept(
        self,
        job_id: str,
        generation: int,
        item_id: str,
        operation_id: str,
        track: TrackInput,
        inspection: Inspection,
        location: Path,
        managed: bool,
        collection_id: str | None,
        decision: str | None,
    ) -> None:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.generation != generation or job.state != "running":
                return
            key = recording_key(track.artist, track.title, track.version)
            recording = session.scalar(select(Recording).where(Recording.identity_key == key))
            if not recording:
                recording = Recording(
                    id=new_id("rec"),
                    identity_key=key,
                    artist=track.artist,
                    title=track.title,
                    version=track.version,
                    evidence={
                        "method": "user_override"
                        if decision
                        else ("embedded_tags" if inspection.title else "supplied_labels"),
                        "acoustic_identity_verified": False,
                    },
                )
                session.add(recording)
                session.flush()
            revision = session.scalar(
                select(AssetRevision).where(AssetRevision.sha256 == inspection.sha256)
            )
            reused = revision is not None
            if revision:
                asset = require(session, Asset, revision.asset_id)
                if asset.recording_id != recording.id:
                    raise AppError(
                        "EXISTING_IDENTITY_CONFLICT",
                        "These bytes already have another catalog identity.",
                    )
            else:
                asset = Asset(
                    id=new_id("asset"),
                    recording_id=recording.id,
                    provenance=require(session, JobItem, item_id).request.get("provenance")
                    or {"kind": "user_supplied", "source": track.path},
                )
                session.add(asset)
                session.flush()
                revision = AssetRevision(
                    id=new_id("rev"),
                    asset_id=asset.id,
                    sha256=inspection.sha256,
                    properties={**inspection.as_dict(), "provenance": asset.provenance},
                )
                session.add(revision)
                session.flush()
            existing = session.scalar(
                select(FileLocation).where(FileLocation.path == str(location))
            )
            if existing and existing.revision_id != revision.id:
                raise AppError(
                    "LOCATION_CHANGED", "This path already refers to different recorded bytes."
                )
            if not existing:
                session.add(
                    FileLocation(
                        id=new_id("location"),
                        revision_id=revision.id,
                        path=str(location),
                        managed=managed,
                    )
                )
            item = require(session, JobItem, item_id)
            if collection_id:
                member = session.scalar(
                    select(Membership).where(
                        Membership.collection_id == collection_id,
                        Membership.recording_id == recording.id,
                    )
                )
                if not member:
                    session.add(
                        Membership(
                            id=new_id("member"),
                            collection_id=collection_id,
                            recording_id=recording.id,
                            revision_id=revision.id,
                            position=item.position,
                        )
                    )
                    require(session, Collection, collection_id).revision += 1
            item.state = "succeeded"
            item.result = {
                "asset_revision_id": revision.id,
                "recording_id": recording.id,
                "reused": reused,
                "path": str(location),
            }
            require(session, Operation, operation_id).phase = "committed"
            add_event(session, job, "item_ready", {"item_id": item.id, "reused": reused})

    async def export(self, job_id: str, generation: int, snapshot: dict) -> None:
        for track in snapshot["tracks"]:
            path = self.app.workspace.authorize(track["path"])
            observed = await asyncio.to_thread(checksum, path)
            if not self.active(job_id, generation):
                return
            if observed != track["sha256"]:
                raise AppError(
                    "RECONCILIATION_REQUIRED",
                    "A collection file changed; restore it or rebuild from a new stable path.",
                )
        export_dir = self.app.workspace.exports / job_id
        manifest = {
            "schema_version": "1",
            "export_job_id": job_id,
            "collection": snapshot,
            "app_state": "prepared_for_import",
            "device_state": "not_exported",
            "verification_method": "local_hash_readback",
        }
        atomic_json(export_dir / "manifest.json", manifest)
        # Track labels are validated against control characters to keep M3U records unambiguous.
        playlist = "#EXTM3U\n" + "".join(
            f"#EXTINF:-1,{t['artist']} - {t['title']}\n{t['path']}\n" for t in snapshot["tracks"]
        )
        atomic_text(export_dir / "collection.m3u8", playlist)
        atomic_text(export_dir / "rekordbox.xml", rekordbox_xml(snapshot))
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state == "running" and job.generation == generation:
                job.state, job.outcome = "completed", "complete"
                job.result = {
                    "manifest_path": str(export_dir / "manifest.json"),
                    "playlist_path": str(export_dir / "collection.m3u8"),
                    "rekordbox_xml_path": str(export_dir / "rekordbox.xml"),
                    "track_count": len(snapshot["tracks"]),
                    "app_state": "prepared_for_import",
                    "device_state": "not_exported",
                }
                add_event(session, job, "export_prepared", {"track_count": len(snapshot["tracks"])})
