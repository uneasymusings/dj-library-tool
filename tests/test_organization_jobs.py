"""Acceptance, checkpoints and cancellation precede expensive organization reads."""

from sqlalchemy import func, select

from djlib.application import organization as org
from djlib.domain.organization_contracts import OrganizationRequest
from djlib.jobs.worker import Worker
from djlib.persistence.models import Collection, Job, JobItem
from tests.conftest import execute
from tests.test_organization import catalog


def request(refs, key="durable-organization"):
    return OrganizationRequest(name="Durable sorted selection", tracks=refs, idempotency_key=key)


async def test_acceptance_does_not_read_audio_and_replay_keeps_frozen_intent(
    application, audio_factory, monkeypatch
):
    _, refs = await catalog(application, audio_factory, [{}, {}])

    def forbidden(*args, **kwargs):
        raise AssertionError("Acceptance must not inspect audio")

    with monkeypatch.context() as patch:
        patch.setattr(org, "inspect_metadata", forbidden)
        accepted = org.organize_collection(application, request(refs))
        replay = org.organize_collection(application, request(refs))
        assert accepted["job_id"] == replay["job_id"]
        assert accepted["state"] == "queued"
        assert accepted["counts"] == {"pending": 2}
        assert "collection_id" not in accepted["result"]
    completed = await execute(application, accepted["job_id"])
    assert completed["outcome"] == "complete"
    assert completed["result"]["selected_count"] == 2


async def test_pause_during_read_fences_result_then_resume_creates_one_collection(
    application, audio_factory, monkeypatch
):
    _, refs = await catalog(application, audio_factory, [{}, {}])
    accepted = org.organize_collection(application, request(refs))
    inspector = org.inspect_metadata
    paused = False

    def pause_once(*args, **kwargs):
        nonlocal paused
        result = inspector(*args, **kwargs)
        if not paused:
            paused = True
            application.control(accepted["job_id"], "pause")
        return result

    monkeypatch.setattr(org, "inspect_metadata", pause_once)
    result = await execute(application, accepted["job_id"])
    assert result["state"] == "paused"
    assert "collection_id" not in result["result"]
    assert result["counts"] == {"pending": 2}
    application.control(accepted["job_id"], "resume")
    resumed = await execute(application, accepted["job_id"])
    assert resumed["outcome"] == "complete"
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Collection)) == 2
    assert org.organize_collection(application, request(refs))["job_id"] == accepted["job_id"]


async def test_cancelled_attempt_does_not_publish_collection(
    application, audio_factory, monkeypatch
):
    _, refs = await catalog(application, audio_factory, [{}])
    accepted = org.organize_collection(application, request(refs))
    inspector = org.inspect_metadata

    def cancel(*args, **kwargs):
        result = inspector(*args, **kwargs)
        application.control(accepted["job_id"], "cancel")
        return result

    monkeypatch.setattr(org, "inspect_metadata", cancel)
    cancelled = await execute(application, accepted["job_id"])
    assert cancelled["state"] == "cancelled"
    assert "collection_id" not in cancelled["result"]
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Collection)) == 1


async def test_restart_keeps_completed_item_checkpoint(application, audio_factory, monkeypatch):
    _, refs = await catalog(application, audio_factory, [{}, {}])
    accepted = org.organize_collection(application, request(refs))
    from djlib.application.organization_jobs import prepare_organization_item

    with application.db.transaction() as session:
        job = session.get(Job, accepted["job_id"])
        job.state, job.generation = "running", 1
        item = session.scalar(
            select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.position)
        )
        item_id = item.id
    await prepare_organization_item(application, accepted["job_id"], 1, item_id)
    Worker(application).recover()
    inspector = org.inspect_metadata
    seen = []

    def record(app, recording_id, asset_revision_id):
        seen.append(recording_id)
        return inspector(app, recording_id, asset_revision_id)

    monkeypatch.setattr(org, "inspect_metadata", record)
    completed = await execute(application, accepted["job_id"])
    assert completed["outcome"] == "complete"
    assert seen == [refs[1]["recording_id"]]
