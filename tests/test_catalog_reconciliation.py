"""Generated originals reproduce identity collisions and explicit byte reconciliation."""

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import COMM
from mutagen.wave import WAVE
from sqlalchemy import func, select

from djlib.application.delivery import create_delivery, delivery_status
from djlib.application.organization import annotate_recording, annotation_status
from djlib.application.reconciliation import reconcile_files
from djlib.application.requests import create_request, get_request
from djlib.audio.inspection import checksum
from djlib.domain.contracts import (
    CollectionRequest,
    DeliveryRequest,
    StartRequest,
    TrackInput,
    recording_key,
)
from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import AnnotationRequest
from djlib.domain.reconciliation_contracts import ReconcileRequest
from djlib.domain.request_contracts import RequestCreate
from djlib.jobs.worker import Worker
from djlib.persistence.models import (
    AssetRevision,
    Delivery,
    FileLocation,
    Job,
    JobItem,
    Membership,
    Recording,
)
from tests.conftest import execute, submit_collection


async def indexed(app, path):
    accepted = submit_collection(app, [path])
    completed = await execute(app, accepted["job_id"])
    assert completed["outcome"] == "complete"
    item = app.items(accepted["job_id"])["items"][0]["result"]
    return item, completed["result"]["collection_id"]


def retag(path, comment="New analysis note"):
    media = WAVE(path)
    if media.tags is None:
        media.add_tags()
    media.tags.add(COMM(encoding=3, lang="eng", desc="test", text=comment))
    media.save()


def request_for(path, old, action="tag_only", key="reconcile"):
    return ReconcileRequest(
        items=[
            {
                "path": str(path),
                "expected_asset_revision_id": old["asset_revision_id"],
                "expected_sha256": checksum(path),
                "action": action,
            }
        ],
        idempotency_key=key,
    )


def only_result(app, job):
    return app.items(job["job_id"])["items"][0]["result"]


async def test_symbol_only_names_remain_distinct_in_collection_and_request_matching(
    application, audio_factory
):
    tracks = [
        TrackInput(
            path=str(
                audio_factory(f"symbol-{i}.wav", frequency=220 + i * 90, artist=glyph, title=glyph)
            ),
            artist=glyph,
            title=glyph,
        )
        for i, glyph in enumerate(("☼", "☾"))
    ]
    plan = application.plan(CollectionRequest(name="Symbol performers", tracks=tracks))
    job = application.start(
        StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key="glyphs")
    )
    completed = await execute(application, job["job_id"])
    assert completed["counts"] == {"succeeded": 2}
    collection = application.collection(completed["result"]["collection_id"])
    assert len({track["recording_id"] for track in collection["tracks"]}) == 2
    report = create_request(
        application,
        RequestCreate(
            name="Glyph request",
            idempotency_key="glyph-ledger",
            items=[{"artist": t.artist, "title": t.title} for t in tracks],
        ),
    )
    assert report["counts"]["satisfied"] == 2
    assert len(report["unique_satisfied_recording_ids"]) == 2
    assert recording_key("|", "%") != recording_key("%", "|")


@pytest.mark.parametrize("artist", ["", "Known performer"])
async def test_incomplete_scan_labels_use_byte_identity_and_reuse_exact_copies(
    application, audio_factory, artist
):
    first = audio_factory("unlabeled/album-a/Track01.wav", frequency=250, artist=artist)
    audio_factory("unlabeled/album-b/Track01.wav", frequency=450, artist=artist)
    duplicate = first.parent / "Renamed.wav"
    shutil.copyfile(first, duplicate)
    scan = application.scan(str(first.parent.parent), "unlabeled")
    completed = await execute(application, scan["job_id"])
    assert completed["counts"] == {"succeeded": 3}
    with application.db.transaction() as session:
        records = list(session.scalars(select(Recording)))
        assert len(records) == 2
        assert all(r.identity_key.startswith("provisional:sha256:") for r in records)
        assert all(r.evidence["method"] == "provisional_bytes" for r in records)
        assert session.scalar(select(func.count()).select_from(FileLocation)) == 3
    repeated = application.scan(str(first.parent.parent), "unlabeled-again")
    assert (await execute(application, repeated["job_id"]))["counts"] == {"succeeded": 3}
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Recording)) == 2


async def test_legacy_bad_identity_is_not_rewritten_by_rescan(application, audio_factory):
    path = audio_factory("historical/original.wav", artist="☼", title="☼")
    first = application.scan(str(path.parent), "legacy-scan")
    await execute(application, first["job_id"])
    with application.db.transaction() as session:
        record = session.scalar(select(Recording))
        old_id = record.id
        record.identity_key = "||"  # Reproduce the identity stored by the previous alpha.
    audio_factory("historical/other.wav", frequency=500, artist="☾", title="☾")
    second = application.scan(str(path.parent), "legacy-rescan")
    assert (await execute(application, second["job_id"]))["outcome"] == "complete"
    with application.db.transaction() as session:
        old = session.get(Recording, old_id)
        assert (old.identity_key, old.artist, old.title) == ("||", "☼", "☼")
        assert session.scalar(select(func.count()).select_from(Recording)) == 2


async def test_tag_only_creates_revision_preserving_ids_notes_membership_and_bytes(
    application, audio_factory
):
    path = audio_factory()
    old, collection_id = await indexed(application, path)
    annotate_recording(
        application,
        AnnotationRequest(
            recording_id=old["recording_id"],
            asset_revision_id=old["asset_revision_id"],
            revision=0,
            idempotency_key="notes",
            notes="Long intro",
        ),
    )
    original_hash = checksum(path)
    retag(path)
    changed_hash = checksum(path)
    assert changed_hash != original_hash
    request = request_for(path, old)
    accepted = reconcile_files(application, request)
    assert accepted["state"] == "queued" and accepted["counts"] == {"pending": 1}
    assert reconcile_files(application, request)["job_id"] == accepted["job_id"]
    completed = await execute(application, accepted["job_id"])
    assert completed["outcome"] == "complete"
    result = only_result(application, completed)
    assert result["recording_id"] == old["recording_id"]
    assert result["asset_revision_id"] != old["asset_revision_id"]
    assert result["source_modified"] is False and checksum(path) == changed_hash
    assert not result["annotations_copied"] and not result["memberships_upgraded"]
    with application.db.transaction() as session:
        before = session.get(AssetRevision, old["asset_revision_id"])
        after = session.get(AssetRevision, result["asset_revision_id"])
        assert before.asset_id == after.asset_id and before.sha256 == original_hash
        assert before.properties["last_known_path"] == str(path)
        assert (
            session.scalar(
                select(Membership).where(Membership.collection_id == collection_id)
            ).revision_id
            == before.id
        )
        assert (
            session.scalar(select(FileLocation).where(FileLocation.path == str(path))).revision_id
            == after.id
        )
    assert (
        annotation_status(application, old["recording_id"], old["asset_revision_id"])[
            "annotations"
        ]["notes"]
        == "Long intro"
    )
    assert (
        annotation_status(application, old["recording_id"], result["asset_revision_id"])[
            "annotations"
        ]
        == {}
    )
    assert reconcile_files(application, request)["job_id"] == accepted["job_id"]
    path.rename(path.with_name("moved-after-reconciliation.wav"))
    assert reconcile_files(application, request)["job_id"] == accepted["job_id"]


async def test_changed_audio_rejected_as_tags_and_explicit_replacement_gets_new_identity(
    application, audio_factory
):
    path = audio_factory()
    old, _ = await indexed(application, path)
    audio_factory(frequency=880)
    accepted = reconcile_files(application, request_for(path, old))
    assert (await execute(application, accepted["job_id"]))["outcome"] == "completed_with_gaps"
    assert only_result(application, accepted)["error"]["code"] == "AUDIO_CHANGED"
    replacement = reconcile_files(application, request_for(path, old, "replace_audio", "replace"))
    assert (await execute(application, replacement["job_id"]))["outcome"] == "complete"
    result = only_result(application, replacement)
    assert result["recording_id"] != old["recording_id"]
    with application.db.transaction() as session:
        assert session.get(Recording, result["recording_id"]).identity_key.startswith(
            "provisional:"
        )
        assert session.get(AssetRevision, old["asset_revision_id"]) is not None
        assert session.scalar(select(Membership)).recording_id == old["recording_id"]


async def test_competing_reconciliation_intents_use_location_cas(application, audio_factory):
    path = audio_factory()
    old, _ = await indexed(application, path)
    retag(path)
    first = reconcile_files(application, request_for(path, old, key="first"))
    second = reconcile_files(application, request_for(path, old, key="second"))
    assert (await execute(application, first["job_id"]))["outcome"] == "complete"
    assert (await execute(application, second["job_id"]))["outcome"] == "completed_with_gaps"
    assert only_result(application, second)["error"]["code"] == "RECONCILIATION_STALE"
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AssetRevision)) == 2
    with pytest.raises(AppError, match="different request"):
        reconcile_files(application, request_for(path, old, "replace_audio", "first"))


async def test_simultaneous_clients_accept_one_durable_idempotent_intent(
    application, audio_factory
):
    path = audio_factory()
    old, _ = await indexed(application, path)
    retag(path)
    request = request_for(path, old)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return reconcile_files(application, request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        jobs = [future.result(timeout=10) for future in futures]
    assert jobs[0]["job_id"] == jobs[1]["job_id"]
    assert (await execute(application, jobs[0]["job_id"]))["counts"] == {"succeeded": 1}
    with application.db.transaction() as session:
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.kind == "reconcile"))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(AssetRevision)) == 2


async def test_new_byte_expectation_and_same_size_path_swap_fail_closed(
    application, audio_factory, monkeypatch
):
    path = audio_factory()
    old, _ = await indexed(application, path)
    accepted = reconcile_files(application, request_for(path, old, "replace_audio"))
    audio_factory(frequency=660)
    await execute(application, accepted["job_id"])
    assert only_result(application, accepted)["error"]["code"] == "RECONCILIATION_BYTES_CHANGED"
    import djlib.application.reconciliation as module

    original = module.inspect_catalog_audio
    swap = audio_factory("swap.wav", frequency=880)
    expected_hash = checksum(path)

    def inspect_and_swap(value):
        result = original(value)
        before = value.stat()
        os.replace(swap, value)
        os.utime(value, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(module, "inspect_catalog_audio", inspect_and_swap)
    changed = reconcile_files(application, request_for(path, old, "replace_audio", "swap"))
    await execute(application, changed["job_id"])
    assert only_result(application, changed)["error"]["code"] == "FILE_CHANGED"
    assert checksum(path) != expected_hash
    with application.db.transaction() as session:
        assert (
            session.scalar(select(FileLocation).where(FileLocation.path == str(path))).revision_id
            == old["asset_revision_id"]
        )


async def test_pause_and_recovery_fence_reconciliation_commit(
    application, audio_factory, monkeypatch
):
    path = audio_factory()
    old, _ = await indexed(application, path)
    retag(path)
    accepted = reconcile_files(application, request_for(path, old))
    import djlib.application.reconciliation as module

    original = module.inspect_catalog_audio

    def paused(value):
        result = original(value)
        application.control(accepted["job_id"], "pause")
        return result

    monkeypatch.setattr(module, "inspect_catalog_audio", paused)
    assert (await execute(application, accepted["job_id"]))["state"] == "paused"
    with application.db.transaction() as session:
        assert (
            session.scalar(select(FileLocation).where(FileLocation.path == str(path))).revision_id
            == old["asset_revision_id"]
        )
    monkeypatch.setattr(module, "inspect_catalog_audio", original)
    application.control(accepted["job_id"], "resume")
    with application.db.transaction() as session:
        job = session.get(Job, accepted["job_id"])
        job.state = "running"
        session.scalar(select(JobItem).where(JobItem.job_id == job.id)).state = "running"
    Worker(application).recover()
    assert (await execute(application, accepted["job_id"]))["outcome"] == "complete"


async def test_legacy_tag_only_requires_verified_original_payload(application, audio_factory):
    path = audio_factory()
    old, _ = await indexed(application, path)
    backup = path.with_name("original-copy.wav")
    shutil.copyfile(path, backup)
    with application.db.transaction() as session:
        revision = session.get(AssetRevision, old["asset_revision_id"])
        revision.properties = {k: v for k, v in revision.properties.items() if k != "audio_payload"}
    retag(path)
    accepted = reconcile_files(application, request_for(path, old))
    await execute(application, accepted["job_id"])
    assert only_result(application, accepted)["error"]["code"] == "AUDIO_BASELINE_UNAVAILABLE"
    scan = application.scan(str(path.parent), "baseline")
    await execute(application, scan["job_id"])
    application.control(accepted["job_id"], "retry")
    assert (await execute(application, accepted["job_id"]))["outcome"] == "complete"


async def test_reconciliation_invalidates_dependent_evidence_without_rewriting_history(
    application, audio_factory
):
    path = audio_factory()
    old, collection_id = await indexed(application, path)
    ledger = create_request(
        application,
        RequestCreate(
            name="Owned",
            idempotency_key="ledger",
            items=[{"artist": "Test Artist", "title": path.stem}],
        ),
    )
    assert ledger["counts"]["satisfied"] == 1
    delivery = create_delivery(
        application,
        DeliveryRequest(
            name="Frozen",
            collection_ids=[collection_id],
            workflow="rekordbox_import",
            app_version="7.2.19",
        ),
    )
    previous_evidence = {"imported": {"outcome": "passed", "observer": "test operator"}}
    with application.db.transaction() as session:
        session.get(Delivery, delivery["delivery_id"]).evidence = previous_evidence
    export = application.export(collection_id, "export")
    await execute(application, export["job_id"])
    retag(path)
    accepted = reconcile_files(application, request_for(path, old))
    assert (await execute(application, accepted["job_id"]))["outcome"] == "complete"
    result = only_result(application, accepted)
    assert result["invalidated_evidence"]["deliveries"][0]["evidence"] == previous_evidence
    status = delivery_status(application, delivery["delivery_id"])
    assert status["revision"] == delivery["revision"] + 1
    assert not status["requirements_met_at_last_check"] and "imported" in status["blockers"]
    assert "catalog_reconciliation" in status["evidence"]
    assert get_request(application, ledger["request_id"])["counts"]["unavailable"] == 1
    historical_export = application.job(export["job_id"])
    assert historical_export["outcome"] == "complete"
    assert (
        historical_export["result"]["catalog_reconciliation"]["old_asset_revision_id"]
        == old["asset_revision_id"]
    )


@pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="Real compressed payload verification requires FFmpeg"
)
async def test_flac_tag_edit_preserves_audio_payload_identity(application, audio_factory):
    source = audio_factory()
    path = source.with_suffix(".flac")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), str(path)], check=True)
    old, _ = await indexed(application, path)
    media = FLAC(path)
    media["comment"] = "Analyzed without changing audio"
    media.save()
    changed = checksum(path)
    accepted = reconcile_files(application, request_for(path, old))
    assert (await execute(application, accepted["job_id"]))["outcome"] == "complete"
    assert only_result(application, accepted)["recording_id"] == old["recording_id"]
    assert checksum(path) == changed
