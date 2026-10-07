"""Bounded background work with generation-fenced commits and an ingestion journal."""

import asyncio
import logging
import os
import shutil
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select

from djlib.application.service import Application, add_event, new_id, require
from djlib.application.tracklists import file_name_labels
from djlib.audio.catalog_inspection import inspect_catalog_audio
from djlib.audio.inspection import SUPPORTED_EXTENSIONS, Inspection, checksum, inspect_audio, labels
from djlib.audio.preparation import tag_download_copy
from djlib.domain.contracts import (
    Profile,
    TrackInput,
    label_form,
    normalize,
    recording_key,
    version_markers,
)
from djlib.domain.errors import AppError
from djlib.exporting.handoff import atomic_text, playlist_label, rekordbox_xml
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

# Failures that belong to one upload, not to the recording: another upload of it may work.
GONE = frozenset({"SOURCE_UNAVAILABLE", "SOURCE_AUTH_REQUIRED", "DOWNLOAD_UNAVAILABLE"})


class Worker:
    """One scheduling authority; slow audio/filesystem work runs outside transactions."""

    HANDOFF_KINDS = frozenset({"collection", "delivery", "delivery_check", "export", "organize"})
    HANDOFF_BURST = 3
    LOCAL_ITEM_QUANTUM = 8
    # A scan lists every file before indexing, about 2 KB of memory a file at its peak;
    # 50,000 files take about 100 MB, so this bounds a scan to about 200 MB.
    SCAN_FILE_LIMIT = 100_000
    # Scan items are written and read in batches so memory and SQL stay bounded.
    SCAN_BATCH = 2_000

    # Decoding dominates catalog work; overlap a few files on separate cores.
    PREFETCH_WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))

    def __init__(self, application: Application):
        self.app = application
        self._handoff_turns = 0
        self._pool = ThreadPoolExecutor(
            max_workers=self.PREFETCH_WORKERS, thread_name_prefix="djlib-inspect"
        )
        self._prefetched: dict[str, tuple[str, Future]] = {}

    def _known(self, sha256: str) -> dict | None:
        """Stored properties for bytes already in the catalog (their decode cannot differ)."""
        with self.app.db.transaction() as session:
            revision = session.scalar(
                select(AssetRevision).where(AssetRevision.sha256 == sha256).limit(1)
            )
            return dict(revision.properties) if revision is not None else None

    def _inspect_later(self, path: str):
        source = self.app.workspace.authorize(path)
        return str(source), inspect_catalog_audio(source, inspect_audio, self._known)

    def _prefetch(self, items: list[tuple[str, str]]) -> None:
        for item_id, path in items:
            if item_id not in self._prefetched:
                self._prefetched[item_id] = (path, self._pool.submit(self._inspect_later, path))

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
            attempt = self._claim_next()
            if attempt is None:
                await asyncio.sleep(0.1)
                continue
            job_id, generation = attempt
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
            # Empty exports and already-finished jobs may not otherwise suspend.
            await asyncio.sleep(0)

    def _claim_next(self) -> tuple[str, int] | None:
        with self.app.db.transaction() as session:
            # Requeue events move a serviced job behind waiting peers. This order
            # survives coordinator restarts without a second, in-memory work queue.
            queued = (
                select(Job)
                .where(Job.state == "queued")
                .order_by(Job.updated_at, Job.created_at, Job.id)
                .limit(1)
            )
            preferred = (
                Job.kind.in_(self.HANDOFF_KINDS)
                if self._handoff_turns < self.HANDOFF_BURST
                else Job.kind.not_in(self.HANDOFF_KINDS)
            )
            job = session.scalar(queued.where(preferred))
            if job is None:
                job = session.scalar(queued)
            if job is None:
                return None
            # A bounded handoff preference keeps exports responsive without
            # starving acquisitions under a stream of new collection requests.
            self._handoff_turns = self._handoff_turns + 1 if job.kind in self.HANDOFF_KINDS else 0
            job.state, job.generation = "running", job.generation + 1
            add_event(session, job, "started")
            return job.id, job.generation

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
                tracks, skipped = await asyncio.to_thread(self.discover, request["path"])
                if not self.active(job_id, generation):
                    return
                with self.app.db.transaction() as session:
                    require(session, Job, job_id).result = {
                        "discovered_files": len(tracks),
                        "skipped_files": skipped,
                    }
                    for position, track in enumerate(tracks):
                        session.add(
                            JobItem(
                                id=new_id("item"),
                                job_id=job_id,
                                position=position,
                                request={"track": track, "identity_decision": None},
                            )
                        )
                        if (position + 1) % self.SCAN_BATCH == 0:
                            # Still one transaction, so an interrupted scan lists nothing.
                            session.flush()
                del tracks  # a large library's file list need not live as long as the scan
        with self.app.db.transaction() as session:
            item_ids = list(
                session.scalars(
                    select(JobItem.id)
                    .where(JobItem.job_id == job_id, JobItem.state == "pending")
                    .order_by(JobItem.position)
                )
            )
        quantum = 1 if kind == "download" else self.LOCAL_ITEM_QUANTUM
        local_paths = self._local_paths(kind, item_ids)
        for processed, item_id in enumerate(item_ids, start=1):
            if not self.active(job_id, generation):
                self._drop_prefetch(item_ids)
                return
            if local_paths:
                upcoming = item_ids[processed - 1 : processed - 1 + 2 * self.PREFETCH_WORKERS]
                self._prefetch([(i, local_paths[i]) for i in upcoming if i in local_paths])
            if kind == "delivery":
                from djlib.application.delivery import prepare_item

                await prepare_item(self.app, job_id, generation, item_id)
            elif kind == "reconcile":
                from djlib.application.reconciliation import reconcile_item

                await reconcile_item(self.app, job_id, generation, item_id)
            elif kind == "organize":
                from djlib.application.organization_jobs import prepare_organization_item

                await prepare_organization_item(self.app, job_id, generation, item_id)
            elif kind == "delivery_check":
                from djlib.application.delivery_checks import prepare_delivery_check_item

                await prepare_delivery_check_item(self.app, job_id, generation, item_id)
            else:
                await self.ingest(job_id, generation, item_id)
            if processed % quantum == 0:
                with self.app.db.transaction() as session:
                    waiting = session.scalar(select(Job.id).where(Job.state == "queued").limit(1))
                if waiting is not None:
                    # Results decoded ahead could go stale before this job's next turn.
                    self._drop_prefetch(item_ids)
                    break
        if self.active(job_id, generation):
            with self.app.db.transaction() as session:
                job = require(session, Job, job_id)
                if job.state != "running" or job.generation != generation:
                    return
                counts = Counter(
                    session.scalars(select(JobItem.state).where(JobItem.job_id == job_id))
                )
                # Yield only at an item checkpoint. This also picks up review
                # resolutions absent from this attempt's original pending snapshot.
                if counts["pending"]:
                    job.state, job.outcome = "queued", None
                    add_event(session, job, "requeued", {"counts": dict(counts)})
                    return
                if kind not in {"delivery", "delivery_check", "organize"}:
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
            if kind == "delivery":
                from djlib.application.delivery import finish_preparation

                await finish_preparation(self.app, job_id, generation)
            elif kind == "organize":
                from djlib.application.organization_jobs import finish_organization

                finish_organization(self.app, job_id, generation)
            elif kind == "delivery_check":
                from djlib.application.delivery_checks import finish_delivery_check

                finish_delivery_check(self.app, job_id, generation)

    def _local_paths(self, kind: str, item_ids: list[str]) -> dict[str, str]:
        """Item → local file for jobs whose first step is inspecting an existing file."""
        if kind not in {"scan", "collection"} or not item_ids:
            return {}
        paths = {}
        with self.app.db.transaction() as session:
            # Batched: SQLite limits how many IDs one query may name.
            for start in range(0, len(item_ids), self.SCAN_BATCH):
                batch = item_ids[start : start + self.SCAN_BATCH]
                for item_id, request in session.execute(
                    select(JobItem.id, JobItem.request).where(JobItem.id.in_(batch))
                ):
                    track = request.get("track")
                    if isinstance(track, dict) and track.get("path"):
                        paths[item_id] = track["path"]
        return paths

    def _drop_prefetch(self, item_ids: list[str]) -> None:
        for item_id in item_ids:
            ahead = self._prefetched.pop(item_id, None)
            if ahead is not None:
                ahead[1].cancel()

    def discover(self, value: str) -> tuple[list[dict], dict]:
        root = self.app.workspace.authorize(value, directory=True)
        workspace = self.app.workspace
        # djlib's own working copies and downloads are never rediscovered as new music,
        # even when the workspace sits inside an allowed music folder.
        generated = [
            folder
            for folder in (
                workspace.exports,
                workspace.staging,
                workspace.incoming,
                workspace.managed,
                workspace.runtime,
            )
            if not root.is_relative_to(folder)
        ]
        tracks, skipped = [], {"workspace_files": 0, "outside_allowed_folders": 0}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if any(path.is_relative_to(folder) for folder in generated):
                skipped["workspace_files"] += 1
                continue
            try:
                authorized = self.app.workspace.authorize(str(path))
            except AppError as exc:
                # A symlink that leaves the allowed folders is skipped, not followed.
                if exc.code != "SOURCE_NOT_ALLOWED":
                    raise
                skipped["outside_allowed_folders"] += 1
                continue
            artist, title = labels(authorized)
            if not (artist or title):
                # Untagged files are often named "Artist - Title"; use that for display.
                # Identity stays byte-based (provisional) because a file name is not a tag.
                artist, title = file_name_labels(path.stem)
            track = TrackInput(
                path=str(authorized), artist=artist or "Unknown artist", title=title or path.stem
            )
            tracks.append(track.model_dump(mode="json"))
            if len(tracks) > self.SCAN_FILE_LIMIT:
                raise AppError(
                    "ITEM_LIMIT",
                    f"A scan is limited to {self.SCAN_FILE_LIMIT:,} music files; "
                    "scan its subfolders one at a time.",
                )
        return tracks, skipped

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
        tried: list[str] = []
        try:
            if "track" not in item_request:
                source_input = item_request["source"]
                if shutil.disk_usage(self.app.workspace.root).free < 1100 * 1024 * 1024:
                    raise AppError(
                        "DISK_RESERVE_REACHED", "Web acquisition requires 1.1 GiB of free space."
                    )
                urls = list(
                    dict.fromkeys([source_input["url"], *source_input.get("alternates", [])])
                )
                for number, url in enumerate(urls):
                    tried.append(url)
                    # Each upload gets its own folder so a resumed receipt names its own audio.
                    folder = item_id if number == 0 else f"{item_id}-{number}"
                    try:
                        fetched = await self.fetch(url, folder, job_id, generation)
                    except AppError as exc:
                        if exc.code in GONE and url != urls[-1]:
                            continue  # this upload is gone; try the next of the same recording
                        raise
                    break
                if fetched is None or not self.active(job_id, generation):
                    return
                path, provenance = fetched
                item_request["track"] = {
                    "path": str(path),
                    **{k: source_input[k] for k in ("artist", "title", "version")},
                }
                item_request["provenance"] = {**provenance, "tried_urls": tried}
                with self.app.db.transaction() as session:
                    require(session, JobItem, item_id).request = item_request
            track = TrackInput.model_validate(item_request["track"])
            source = self.app.workspace.authorize(track.path)
            ahead = self._prefetched.pop(item_id, None)
            if ahead is not None and ahead[0] == track.path:
                inspected_path, (inspection, payload_identity, _) = await asyncio.wrap_future(
                    ahead[1]
                )
                if inspected_path != str(source):
                    raise AppError("FILE_CHANGED", "The source path changed during inspection.")
            else:
                inspection, payload_identity, _ = await asyncio.to_thread(
                    inspect_catalog_audio, source, inspect_audio, self._known
                )
            if not self.active(job_id, generation):
                return
            if job.kind == "scan":
                # Filename fallbacks cannot assign different identities to identical
                # bytes. A scan reuses the catalog's existing byte identity.
                with self.app.db.transaction() as session:
                    existing = session.scalar(
                        select(Recording)
                        .join(Asset)
                        .join(AssetRevision)
                        .where(AssetRevision.sha256 == inspection.sha256)
                    )
                    unnamed = (
                        existing is not None
                        and existing.artist == "Unknown artist"
                        and existing.identity_key.startswith("provisional:")
                        and track.artist != "Unknown artist"
                    )
                    if existing and not unnamed:
                        track = track.model_copy(
                            update={
                                "artist": existing.artist,
                                "title": existing.title,
                                "version": existing.version,
                            }
                        )
                    if existing:
                        decision = "reuse_existing_bytes"
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
                suffix = source.suffix.lower()
                if job.kind == "download" and suffix in {".flac", ".mp3"}:
                    # Original acquisition bytes remain in incoming/. Only the managed
                    # derivative gets chosen labels; its *final* bytes are cataloged.
                    original_hash = inspection.sha256
                    await asyncio.to_thread(tag_download_copy, staging, track, suffix)
                    tagged = staging.with_suffix(suffix)
                    os.replace(staging, tagged)
                    staging = tagged
                    inspection = await asyncio.to_thread(inspect_audio, staging)
                    provenance = {
                        **item_request.get("provenance", {}),
                        "acquired_sha256": original_hash,
                        "managed_labels": "supplied artist/title/version; not acoustic evidence",
                    }
                    item_request["provenance"] = provenance
                    with self.app.db.transaction() as session:
                        require(session, JobItem, item_id).request = item_request
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
                payload_identity,
            )
        except AppError as exc:
            if self.active(job_id, generation):
                with self.app.db.transaction() as session:
                    item = require(session, JobItem, item_id)
                    item.state = "failed"
                    item.result = {
                        "error": exc.as_dict(),
                        **({"tried_urls": tried} if tried else {}),
                    }
                    require(session, Operation, operation_id).phase = "failed"
                    add_event(
                        session,
                        require(session, Job, job_id),
                        "item_failed",
                        {"item_id": item_id, "code": exc.code},
                    )

    async def fetch(
        self, url: str, folder: str, job_id: str, generation: int
    ) -> tuple[Path, dict] | None:
        """Download one upload, or None when the job was paused or cancelled meanwhile."""
        task = asyncio.create_task(download(url, self.app.workspace.incoming / folder))
        try:
            while not task.done():
                if not self.active(job_id, generation):
                    return None
                await asyncio.sleep(0.2)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

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
        payload_identity: dict | None = None,
    ) -> None:
        with self.app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.generation != generation or job.state != "running":
                return
            provisional = job.kind == "scan" and not (
                inspection.artist.strip() and inspection.title.strip()
            )
            key = (
                f"provisional:sha256:{inspection.sha256}"
                if provisional
                else recording_key(track.artist, track.title, track.version)
            )
            revision = session.scalar(
                select(AssetRevision).where(AssetRevision.sha256 == inspection.sha256)
            )
            # A rescan preserves earlier explicit identities, including historical
            # provisional or incorrectly merged labels. It never rewrites them.
            recording = (
                require(session, Recording, require(session, Asset, revision.asset_id).recording_id)
                if revision is not None and job.kind == "scan"
                else session.scalar(select(Recording).where(Recording.identity_key == key))
            )
            if (
                recording is not None
                and job.kind == "scan"
                and provisional
                and recording.artist == "Unknown artist"
                and track.artist != "Unknown artist"
            ):
                # Display labels of an unnamed provisional recording may improve on rescan;
                # its byte identity and every membership stay unchanged.
                recording.artist, recording.title = track.artist, track.title
                recording.evidence = {**recording.evidence, "labels_source": "file_name"}
            if not recording and revision is not None:
                # Identical bytes already cataloged with equivalent labels, e.g. title
                # "Rain (Extended Mix)" for a request of "Rain" + "Extended Mix".
                existing = require(
                    session, Recording, require(session, Asset, revision.asset_id).recording_id
                )
                if existing.evidence.get("labels_are_identity", True) and label_form(
                    existing.artist, existing.title, existing.version
                ) == label_form(track.artist, track.title, track.version):
                    recording = existing
            if not recording:
                recording = Recording(
                    id=new_id("rec"),
                    identity_key=key,
                    artist=track.artist,
                    title=track.title,
                    version=track.version,
                    evidence={
                        **(
                            {"labels_source": "file_name"}
                            if provisional and track.artist != "Unknown artist"
                            else {}
                        ),
                        "method": "provisional_bytes"
                        if provisional
                        else "user_override"
                        if decision
                        else (
                            "supplied_labels"
                            if require(session, JobItem, item_id).request.get("source")
                            else ("embedded_tags" if inspection.title else "supplied_labels")
                        ),
                        "acoustic_identity_verified": False,
                        "labels_are_identity": not provisional,
                    },
                )
                session.add(recording)
                session.flush()
            reused = revision is not None
            if revision:
                asset = require(session, Asset, revision.asset_id)
                if asset.recording_id != recording.id:
                    raise AppError(
                        "EXISTING_IDENTITY_CONFLICT",
                        f"This file is already cataloged as recording {asset.recording_id} "
                        "under a different identity. Add cataloged tracks to a collection by "
                        "their IDs with 'organize collection'; retag the file and reconcile it "
                        "to change its identity.",
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
                    properties={
                        **inspection.as_dict(),
                        "provenance": asset.provenance,
                        "audio_payload": payload_identity,
                        "indexed_path": str(location),
                    },
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
            result = {
                "asset_revision_id": revision.id,
                "recording_id": recording.id,
                "reused": reused,
                "path": str(location),
            }
            if source := item.request.get("source"):
                # Which upload arrived, and every one tried before it.
                provenance = item.request.get("provenance") or {}
                result["source_url"] = provenance.get("source_url") or source["url"]
                result["tried_urls"] = provenance.get("tried_urls") or [source["url"]]
            item.state, item.result = "succeeded", result
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
            f"#EXTINF:-1,{playlist_label(t)}\n{t['path']}\n" for t in snapshot["tracks"]
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
