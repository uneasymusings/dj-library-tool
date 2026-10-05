"""Durable native-check boundaries exercised with original tones and real worker checks."""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from djlib.application import delivery
from djlib.application import delivery_checks as checks
from djlib.audio.inspection import checksum
from djlib.domain.errors import AppError
from djlib.jobs.worker import Worker
from djlib.persistence.models import Job, JobItem
from tests.conftest import execute
from tests.test_app_delivery import add_analysis_tags, analysis_observation, observe, prepared
from tests.test_delivery_workflow import observation, prepared_delivery
from tests.test_delivery_workflow import simulated_device as simulated_device

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Original working-copy fixtures require real FFmpeg and ffprobe",
)


def claim(app, job_id):
    with app.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state, job.generation = "running", job.generation + 1
        generation = job.generation
        items = list(session.scalars(select(JobItem.id).where(JobItem.job_id == job_id)))
    return generation, items


async def imported(app, factory):
    source, digest, status, manifest = await prepared(app, factory)
    return source, digest, observe(app, status, manifest), manifest


def current(app, status):
    return delivery.delivery_status(app, status["delivery_id"])


def assert_unchanged(app, expected):
    actual = current(app, expected)
    assert actual["revision"] == expected["revision"]
    assert actual["evidence"] == expected["evidence"]


async def test_analysis_accepts_durable_intent_before_audio_checks_and_replays_after_cas(
    application, audio_factory, monkeypatch
):
    source, digest, status, manifest = await imported(application, audio_factory)
    working = Path(manifest["tracks"][0]["path"])
    add_analysis_tags(working)
    observation = analysis_observation(status, manifest)

    def manifest_only(path):
        assert Path(path).name == "delivery-manifest.json", "Acceptance must not hash audio"
        return checksum(path)

    def no_decode(*args, **kwargs):
        pytest.fail("Acceptance must persist intent before decoding or reconciling audio")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "checksum", manifest_only)
        patch.setattr(delivery, "inspect_audio", no_decode)
        patch.setattr(delivery, "pcm_hash", no_decode)
        patch.setattr(delivery, "_reconcile_analysis_track", no_decode)
        accepted = checks.submit_observation(application, status["delivery_id"], observation)
        assert accepted["kind"] == "delivery_check" and accepted["state"] == "queued"
        assert accepted["counts"] == {"pending": 1}
        assert_unchanged(application, status)
    completed = await execute(application, accepted["job_id"])
    assert completed["state"] == "completed" and completed["outcome"] == "complete"
    assert completed["counts"] == {"succeeded": 1}
    assert completed["result"]["evidence_committed"] is True
    latest = current(application, status)
    assert latest["revision"] == status["revision"] + 1
    assert latest["evidence"]["analyzed"]["assets"][0]["sha256"] == checksum(working)
    assert not latest["ready_for_app_use"]
    replay = checks.submit_observation(application, status["delivery_id"], observation)
    assert replay["job_id"] == accepted["job_id"] and replay["state"] == "completed"
    assert_unchanged(application, latest)
    assert checksum(source) == digest


async def test_app_verification_completion_records_check_without_replay_claiming_freshness(
    application, audio_factory, monkeypatch
):
    source, digest, status, manifest = await imported(application, audio_factory)
    status = observe(application, status, manifest, "analyzed")
    accepted = checks.submit_app_verification(
        application, status["delivery_id"], status["revision"]
    )
    assert accepted["state"] == "queued"
    assert "app_readback" not in status["evidence"]
    completed = await execute(application, accepted["job_id"])
    assert completed["state"] == "completed" and completed["result"]["evidence_committed"]
    latest = current(application, status)
    readback = latest["evidence"]["app_readback"]
    assert readback["status"] == "verified_working_files"
    assert readback["assets"][0]["sha256"] == checksum(Path(manifest["tracks"][0]["path"]))
    assert latest["revision"] == status["revision"] + 1
    assert latest["app_requirements_met_at_last_check"]
    assert not latest["ready_for_app_use"] and not latest["hashes_rechecked_by_status"]

    def no_new_check(*args, **kwargs):
        pytest.fail("Replaying a finished verification must return the historical job")

    monkeypatch.setattr(delivery, "_prepared", no_new_check)
    replay = checks.submit_app_verification(application, status["delivery_id"], status["revision"])
    assert replay == completed
    assert not replay["result"].get("ready_for_app_use", False)
    assert_unchanged(application, latest)
    assert checksum(source) == digest


@pytest.mark.parametrize("operation", ["analyzed", "verify-app"])
@pytest.mark.parametrize("fence", ["pause", "cancel", "generation"])
async def test_control_at_final_commit_fences_delivery_evidence(
    application, audio_factory, monkeypatch, operation, fence
):
    _, _, status, manifest = await imported(application, audio_factory)
    if operation == "verify-app":
        status = observe(application, status, manifest, "analyzed")
        accepted = checks.submit_app_verification(
            application, status["delivery_id"], status["revision"]
        )
    else:
        accepted = checks.submit_observation(
            application, status["delivery_id"], analysis_observation(status, manifest)
        )
    generation, items = claim(application, accepted["job_id"])
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    original = delivery._save_evidence

    def interrupt_commit(*args, **kwargs):
        if fence == "generation":
            with application.db.transaction() as session:
                session.get(Job, accepted["job_id"]).generation += 1
        else:
            application.control(accepted["job_id"], fence)
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery, "_save_evidence", interrupt_commit)
    checks.finish_delivery_check(application, accepted["job_id"], generation)
    assert_unchanged(application, status)
    assert (
        application.job(accepted["job_id"])["state"]
        == {"pause": "paused", "cancel": "cancelled", "generation": "running"}[fence]
    )


@pytest.mark.parametrize("change", ["tags", "audio", "missing"])
async def test_failed_working_bytes_revoke_prior_analysis_and_readback_atomically(
    application, audio_factory, change
):
    source, digest, status, manifest = await imported(application, audio_factory)
    status = observe(application, status, manifest, "analyzed")
    status = delivery.verify_app(application, status["delivery_id"], status["revision"])
    assert status["ready_for_app_use"]
    accepted = checks.submit_app_verification(
        application, status["delivery_id"], status["revision"]
    )
    working = Path(manifest["tracks"][0]["path"])
    if change == "tags":
        add_analysis_tags(working)
    elif change == "audio":
        shutil.copyfile(audio_factory("replacement.wav", frequency=660, frames=88200), working)
    else:
        working.unlink()
    completed = await execute(application, accepted["job_id"])
    assert completed["state"] == "failed" and completed["outcome"] is None
    assert completed["counts"] == {"failed": 1}
    latest = current(application, status)
    assert latest["revision"] == status["revision"] + 1
    assert latest["evidence"]["analyzed"]["outcome"] == "failed"
    assert latest["evidence"]["app_readback"]["status"] == "failed"
    assert not latest["app_requirements_met_at_last_check"]
    assert completed["result"]["error"] == latest["evidence"]["analyzed"]["error"]
    assert checksum(source) == digest


@pytest.mark.parametrize("fence", ["pause", "cancel", "generation"])
async def test_failed_item_invalidation_is_also_fenced_at_commit(
    application, audio_factory, monkeypatch, fence
):
    _, _, status, manifest = await imported(application, audio_factory)
    status = observe(application, status, manifest, "analyzed")
    accepted = checks.submit_app_verification(
        application, status["delivery_id"], status["revision"]
    )
    Path(manifest["tracks"][0]["path"]).unlink()
    generation, items = claim(application, accepted["job_id"])
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    original = checks._fail_job

    def interrupt_invalidation(*args, **kwargs):
        if fence == "generation":
            with application.db.transaction() as session:
                session.get(Job, accepted["job_id"]).generation += 1
        else:
            application.control(accepted["job_id"], fence)
        return original(*args, **kwargs)

    monkeypatch.setattr(checks, "_fail_job", interrupt_invalidation)
    checks.finish_delivery_check(application, accepted["job_id"], generation)
    assert_unchanged(application, status)
    assert (
        application.job(accepted["job_id"])["state"]
        == {"pause": "paused", "cancel": "cancelled", "generation": "running"}[fence]
    )


@pytest.mark.parametrize("failed", [False, True])
async def test_stale_delivery_revision_never_clobbers_new_native_observations(
    application, audio_factory, failed
):
    _, _, status, manifest = await imported(application, audio_factory)
    accepted = checks.submit_observation(
        application, status["delivery_id"], analysis_observation(status, manifest)
    )
    if failed:
        Path(manifest["tracks"][0]["path"]).unlink()
    generation, items = claim(application, accepted["job_id"])
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    newer = observe(application, status, manifest)
    checks.finish_delivery_check(application, accepted["job_id"], generation)
    assert_unchanged(application, newer)
    finished = application.job(accepted["job_id"])
    assert finished["state"] == "failed"
    if not failed:
        assert finished["result"]["error"]["code"] == "DELIVERY_STALE"


async def test_completed_item_checkpoint_survives_pause_and_coordinator_recovery(
    application, audio_factory, monkeypatch
):
    _, _, status, manifest = await imported(application, audio_factory)
    accepted = checks.submit_observation(
        application, status["delivery_id"], analysis_observation(status, manifest)
    )
    generation, items = claim(application, accepted["job_id"])
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    application.control(accepted["job_id"], "pause")
    checks.finish_delivery_check(application, accepted["job_id"], generation)
    assert_unchanged(application, status)
    application.control(accepted["job_id"], "resume")
    with application.db.transaction() as session:
        session.get(Job, accepted["job_id"]).state = "running"
    Worker(application).recover()
    assert application.job(accepted["job_id"])["counts"] == {"succeeded": 1}

    def no_repeat(*args, **kwargs):
        pytest.fail("A succeeded, stable item checkpoint should not repeat expensive checks")

    monkeypatch.setattr(delivery, "_reconcile_analysis_track", no_repeat)
    finished = await execute(application, accepted["job_id"])
    assert finished["state"] == "completed" and finished["outcome"] == "complete"
    assert current(application, status)["revision"] == status["revision"] + 1


async def test_recovered_checkpoint_rejects_audio_changed_before_finalization(
    application, audio_factory
):
    _, _, status, manifest = await imported(application, audio_factory)
    accepted = checks.submit_observation(
        application, status["delivery_id"], analysis_observation(status, manifest)
    )
    generation, items = claim(application, accepted["job_id"])
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    Worker(application).recover()
    add_analysis_tags(Path(manifest["tracks"][0]["path"]))
    finished = await execute(application, accepted["job_id"])
    assert finished["state"] == "failed"
    assert finished["result"]["error"]["code"] == "RECONCILIATION_STALE"
    latest = current(application, status)
    assert latest["evidence"]["analyzed"]["outcome"] == "failed"
    assert latest["revision"] == status["revision"] + 1


async def test_invalid_operator_method_does_not_revoke_verified_working_files(
    application, audio_factory
):
    _, _, status, manifest = await imported(application, audio_factory)
    status = observe(application, status, manifest, "analyzed")
    status = delivery.verify_app(application, status["delivery_id"], status["revision"])
    invalid = analysis_observation(status, manifest).model_copy(
        update={"method": "physical_hardware"}
    )
    try:
        accepted = checks.submit_observation(application, status["delivery_id"], invalid)
    except AppError as error:
        assert error.code == "EVIDENCE_METHOD"
    else:
        finished = await execute(application, accepted["job_id"])
        assert finished["state"] == "failed"
        assert finished["result"]["error"]["code"] == "EVIDENCE_METHOD"
    assert_unchanged(application, status)
    assert current(application, status)["app_requirements_met_at_last_check"]


@pytest.mark.parametrize("changed", [False, True])
async def test_native_export_is_a_durable_exact_byte_check(
    application, audio_factory, simulated_device, monkeypatch, changed
):
    _, _, status, manifest = await prepared_delivery(application, audio_factory)
    status = delivery.observe_delivery(
        application, status["delivery_id"], observation(status, manifest)
    )
    status = delivery.observe_delivery(
        application, status["delivery_id"], observation(status, manifest, "analyzed")
    )
    status = delivery.bind_device(
        application, status["delivery_id"], status["revision"], simulated_device
    )
    exported = observation(status, manifest, "native_exported")
    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_reconcile_analysis_track", lambda *_: pytest.fail("early hash"))
        accepted = checks.submit_observation(application, status["delivery_id"], exported)
        assert accepted["state"] == "queued"
        assert_unchanged(application, status)
    if changed:
        add_analysis_tags(Path(manifest["tracks"][0]["path"]))
    finished = await execute(application, accepted["job_id"])
    latest = current(application, status)
    assert finished["result"]["evidence_committed"]
    if changed:
        assert finished["state"] == "failed"
        assert finished["result"]["error"]["code"] == "ANALYSIS_CHANGED"
        assert latest["evidence"]["analyzed"]["outcome"] == "failed"
    else:
        assert finished["state"] == "completed"
        assert latest["evidence"]["native_exported"]["outcome"] == "passed"
        assert checks.submit_observation(application, status["delivery_id"], exported) == finished
    assert not latest["ready_for_departure"]
    with pytest.raises(AppError) as error:
        application.control(accepted["job_id"], "retry")
    assert error.value.code == "JOB_TERMINAL"


async def test_completed_app_check_requires_a_new_current_revision_intent(
    application, audio_factory
):
    _, _, status, manifest = await imported(application, audio_factory)
    status = observe(application, status, manifest, "analyzed")
    accepted = checks.submit_app_verification(
        application, status["delivery_id"], status["revision"]
    )
    finished = await execute(application, accepted["job_id"])
    assert finished["state"] == "completed"
    for action in ("pause", "resume", "retry", "cancel"):
        with pytest.raises(AppError) as error:
            application.control(accepted["job_id"], action)
        assert error.value.code == "JOB_TERMINAL"
    latest = current(application, status)
    fresh = checks.submit_app_verification(application, status["delivery_id"], latest["revision"])
    assert fresh["job_id"] != finished["job_id"] and fresh["state"] == "queued"


@pytest.mark.parametrize("interruption", ["pause", "recover"])
async def test_interrupted_running_check_retries_without_publishing_partial_evidence(
    application, audio_factory, monkeypatch, interruption
):
    _, _, status, manifest = await imported(application, audio_factory)
    accepted = checks.submit_observation(
        application, status["delivery_id"], analysis_observation(status, manifest)
    )
    generation, items = claim(application, accepted["job_id"])
    original = delivery._reconcile_analysis_track
    calls = []

    def interrupted(*args, **kwargs):
        checked = original(*args, **kwargs)
        calls.append(checked)
        if len(calls) == 1:
            if interruption == "pause":
                application.control(accepted["job_id"], "pause")
            else:
                Worker(application).recover()
        return checked

    monkeypatch.setattr(delivery, "_reconcile_analysis_track", interrupted)
    await checks.prepare_delivery_check_item(application, accepted["job_id"], generation, items[0])
    assert_unchanged(application, status)
    assert application.job(accepted["job_id"])["counts"] == {"pending": 1}
    if interruption == "pause":
        application.control(accepted["job_id"], "resume")
    finished = await execute(application, accepted["job_id"])
    assert finished["state"] == "completed" and finished["outcome"] == "complete"
    assert len(calls) == 2
    assert current(application, status)["revision"] == status["revision"] + 1
