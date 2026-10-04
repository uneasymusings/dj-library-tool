"""Concurrent delivery requests must preserve evidence and a single preparation job."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import event, select

from djlib.application import delivery
from djlib.domain.contracts import DeliveryRequest
from djlib.domain.errors import AppError
from djlib.persistence.models import Delivery, Event, Job, JobItem, Submission
from tests.conftest import execute, submit_collection


def test_concurrent_evidence_same_revision_has_one_winner(application):
    with application.db.transaction() as session:
        session.add(Delivery(id="concurrent-evidence", request={}, snapshot={}))
    original = delivery._load(application, "concurrent-evidence")
    both_updates = Barrier(2)

    def rendezvous_before_update(conn, cursor, statement, parameters, context, executemany):
        # Rendezvous after any revision reads, immediately before the actual SQL write.
        # A read/check followed by an unconditional UPDATE would lose one observation.
        if statement.lstrip().upper().startswith("UPDATE DELIVERIES "):
            both_updates.wait(timeout=5)

    def save(observer):
        try:
            delivery._save_evidence(application, original, {"observer": observer})
        except AppError as error:
            return observer, error.code
        return observer, "saved"

    engine = application.db.engine
    event.listen(engine, "before_cursor_execute", rendezvous_before_update)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, ["operator-a", "operator-b"]))
    finally:
        event.remove(engine, "before_cursor_execute", rendezvous_before_update)

    assert sorted(result for _, result in outcomes) == ["DELIVERY_STALE", "saved"]
    winner = next(observer for observer, result in outcomes if result == "saved")
    current = delivery._load(application, original["delivery_id"])
    assert current["revision"] == original["revision"] + 1
    assert current["evidence"] == {"observer": winner}


async def test_concurrent_prepare_different_keys_share_one_job(
    application, audio_factory, monkeypatch
):
    tone = audio_factory("concurrent-pilot.wav")
    ingestion = submit_collection(application, [tone], key="concurrent-catalog")
    completed = await execute(application, ingestion["job_id"])
    assert completed["outcome"] == "complete"
    planned = delivery.create_delivery(
        application,
        DeliveryRequest(
            name="Concurrent original-tone pilot",
            collection_ids=[completed["result"]["collection_id"]],
            workflow="serato_portable",
            app_version="synthetic-test-version",
            pilot_size=1,
        ),
    )
    both_loaded = Barrier(2)
    submit = application.submit

    def synchronized_submit(kind, payload, key):
        # Both callers have read the same unbound delivery before either submission.
        both_loaded.wait(timeout=5)
        return submit(kind, payload, key)

    monkeypatch.setattr(application, "submit", synchronized_submit)
    keys = ["concurrent-prepare-a", "concurrent-prepare-b"]

    def prepare(key):
        return delivery.prepare_delivery(
            application, planned["delivery_id"], planned["revision"], key
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(prepare, keys))

    assert jobs[0]["job_id"] == jobs[1]["job_id"]
    job_id = jobs[0]["job_id"]
    with application.db.transaction() as session:
        assert session.get(Delivery, planned["delivery_id"]).job_id == job_id
        assert list(session.scalars(select(Job.id).where(Job.kind == "delivery"))) == [job_id]
        assert len(list(session.scalars(select(JobItem).where(JobItem.job_id == job_id)))) == 1
        submissions = list(session.scalars(select(Submission).where(Submission.key.in_(keys))))
        assert {submission.key for submission in submissions} == set(keys)
        assert {submission.job_id for submission in submissions} == {job_id}
        accepted = list(
            session.scalars(select(Event).where(Event.job_id == job_id, Event.kind == "accepted"))
        )
        assert len(accepted) == 1
