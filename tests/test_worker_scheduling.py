"""Serial, checkpointed scheduling under competing real SQLite jobs."""

import asyncio
import shutil
import sys
from types import SimpleNamespace

from sqlalchemy import select

from djlib.domain.contracts import DownloadRequest, SourceTrack
from djlib.jobs.worker import Worker
from djlib.persistence.models import Job, JobItem
from tests.conftest import execute, submit_collection


class Provider:
    """Controlled downloads of original test tones; no external provider or network."""

    def __init__(self):
        self.sources = {}
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.on_start = None

    async def download(self, url, destination):
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.on_start:
                await self.on_start(url)
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / "audio.wav"
            shutil.copyfile(self.sources[url], path)
            return path, {"kind": "generated_test_tone", "source_url": url}
        finally:
            self.active -= 1

    def submit(self, application, audio_factory, name, count):
        tracks = []
        for n in range(count):
            path = audio_factory(f"{name}-{n}.wav", frequency=300 + sum(map(ord, name)) + n * 37)
            url = f"https://youtu.be/{name}{n}"
            self.sources[url] = path
            tracks.append(SourceTrack(url=url, artist="Test Artist", title=path.stem))
        return application.download(DownloadRequest(name=name, idempotency_key=name, tracks=tracks))


async def wait_for_state(application, job_id, state):
    async with asyncio.timeout(10):
        while application.job(job_id)["state"] != state:
            await asyncio.sleep(0.01)


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_late_collection_and_export_run_before_next_bulk_download(
    application, audio_factory, monkeypatch
):
    ready = submit_collection(application, [audio_factory()], key="ready")
    await execute(application, ready["job_id"])
    provider = Provider()
    first_started, release_first, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observations = []
    collection = export = None

    async def on_start(url):
        if len(provider.calls) == 1:
            first_started.set()
            await release_first.wait()
        elif len(provider.calls) == 2:
            observations.append(
                (application.job(collection["job_id"]), application.job(export["job_id"]))
            )
            second_started.set()

    provider.on_start = on_start
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    bulk = provider.submit(application, audio_factory, "bulk", 5)
    task = asyncio.create_task(Worker(application).run())
    try:
        async with asyncio.timeout(10):
            await first_started.wait()
            collection = submit_collection(application, [audio_factory()], key="handoff")
            export = application.export(ready["result"]["collection_id"], "handoff-export")
            release_first.set()
            await second_started.wait()
        assert all(job["outcome"] == "complete" for job in observations[0])
        await wait_for_state(application, bulk["job_id"], "completed")
        assert application.job(bulk["job_id"])["counts"] == {"succeeded": 5}
        assert provider.max_active == 1
    finally:
        release_first.set()
        await stop(task)


async def test_download_jobs_take_turns_without_parallel_providers(
    application, audio_factory, monkeypatch
):
    provider = Provider()
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    first = provider.submit(application, audio_factory, "first", 3)
    second = provider.submit(application, audio_factory, "second", 3)
    task = asyncio.create_task(Worker(application).run())
    try:
        await wait_for_state(application, first["job_id"], "completed")
        await wait_for_state(application, second["job_id"], "completed")
        assert [url.rsplit("/", 1)[1] for url in provider.calls] == [
            "first0",
            "second0",
            "first1",
            "second1",
            "first2",
            "second2",
        ]
        assert provider.max_active == 1
    finally:
        await stop(task)


async def test_handoff_burst_does_not_starve_waiting_downloads(
    application, audio_factory, monkeypatch
):
    provider = Provider()
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    bulk = provider.submit(application, audio_factory, "background", 2)
    handoffs = [
        submit_collection(application, [audio_factory()], key=f"handoff-{n}")
        for n in range(Worker.HANDOFF_BURST + 2)
    ]
    observed = []

    async def on_start(url):
        observed.append(sum(application.job(j["job_id"])["state"] == "completed" for j in handoffs))

    provider.on_start = on_start
    task = asyncio.create_task(Worker(application).run())
    try:
        await wait_for_state(application, bulk["job_id"], "completed")
        for job in handoffs:
            await wait_for_state(application, job["job_id"], "completed")
        assert observed[0] == Worker.HANDOFF_BURST < len(handoffs)
    finally:
        await stop(task)


async def test_pause_at_checkpoint_survives_restart_without_repeating_success(
    application, audio_factory, monkeypatch
):
    provider = Provider()
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    bulk = provider.submit(application, audio_factory, "pausable", 3)
    original_ingest = Worker.ingest
    paused = False

    async def pause_after_first(self, job_id, generation, item_id):
        nonlocal paused
        await original_ingest(self, job_id, generation, item_id)
        if not paused:
            paused = True
            application.control(job_id, "pause")

    monkeypatch.setattr(Worker, "ingest", pause_after_first)
    task = asyncio.create_task(Worker(application).run())
    try:
        await wait_for_state(application, bulk["job_id"], "paused")
    finally:
        await stop(task)
    assert application.job(bulk["job_id"])["counts"] == {"succeeded": 1, "pending": 2}
    worker = Worker(application)
    worker.recover()
    assert worker._claim_next() is None
    application.control(bulk["job_id"], "resume")
    task = asyncio.create_task(worker.run())
    try:
        await wait_for_state(application, bulk["job_id"], "completed")
        assert len(provider.calls) == len(set(provider.calls)) == 3
        assert application.job(bulk["job_id"])["counts"] == {"succeeded": 3}
    finally:
        await stop(task)


async def test_requeued_order_and_generation_survive_new_worker(
    application, audio_factory, monkeypatch
):
    provider = Provider()
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    first = provider.submit(application, audio_factory, "older", 2)
    second = provider.submit(application, audio_factory, "waiting", 2)
    worker = Worker(application)
    first_id, first_generation = worker._claim_next()
    assert first_id == first["job_id"]
    await worker.execute(first_id, first_generation)
    assert application.job(first_id)["counts"] == {"succeeded": 1, "pending": 1}
    assert not worker.active(first_id, first_generation)
    await worker.execute(first_id, first_generation)
    assert len(provider.calls) == 1  # The yielded generation cannot process another item.
    restarted = Worker(application)
    restarted.recover()
    second_id, generation = restarted._claim_next()
    assert second_id == second["job_id"]
    await restarted.execute(second_id, generation)
    next_id, next_generation = restarted._claim_next()
    assert next_id == first_id and next_generation > first_generation
    await restarted.execute(next_id, next_generation)
    assert application.job(first_id)["counts"] == {"succeeded": 2}


async def test_shutdown_recovers_inflight_download_before_restart(
    application, audio_factory, monkeypatch
):
    provider = Provider()
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def on_start(url):
        started.set()
        await blocked.wait()

    provider.on_start = on_start
    monkeypatch.setattr("djlib.jobs.worker.download", provider.download)
    bulk = provider.submit(application, audio_factory, "restart", 2)
    task = asyncio.create_task(Worker(application).run())
    try:
        async with asyncio.timeout(10):
            await started.wait()
    finally:
        await stop(task)
    assert provider.active == 0
    assert application.job(bulk["job_id"])["state"] == "queued"
    assert application.job(bulk["job_id"])["counts"] == {"pending": 2}
    assert not application.library()["tracks"]
    provider.on_start = None
    task = asyncio.create_task(Worker(application).run())
    try:
        await wait_for_state(application, bulk["job_id"], "completed")
        assert application.job(bulk["job_id"])["counts"] == {"succeeded": 2}
        assert len(application.library()["tracks"]) == 2
        assert provider.max_active == 1
    finally:
        await stop(task)


async def test_delivery_yields_before_finishing_and_uses_delivery_hooks(application, monkeypatch):
    prepared, finished = [], []

    async def prepare_item(app, job_id, generation, item_id):
        assert Worker(app).active(job_id, generation)
        with app.db.transaction() as session:
            session.get(JobItem, item_id).state = "succeeded"
        prepared.append(item_id)

    async def finish_preparation(app, job_id, generation):
        with app.db.transaction() as session:
            states = list(session.scalars(select(JobItem.state).where(JobItem.job_id == job_id)))
            assert set(states) == {"succeeded"}
            job = session.get(Job, job_id)
            assert job.generation == generation and job.state == "running"
            job.state, job.outcome = "completed", "complete"
        finished.append(job_id)

    monkeypatch.setitem(
        sys.modules,
        "djlib.application.delivery",
        SimpleNamespace(prepare_item=prepare_item, finish_preparation=finish_preparation),
    )
    with application.db.transaction() as session:
        session.add(Job(id="delivery", kind="delivery", request={}, result={}))
        session.add(Job(id="background", kind="download", request={}, result={}))
        session.flush()
        for n in range(Worker.LOCAL_ITEM_QUANTUM + 1):
            session.add(JobItem(id=f"delivery-{n}", job_id="delivery", position=n, request={}))
    worker = Worker(application)
    job_id, generation = worker._claim_next()
    assert job_id == "delivery"
    await worker.execute(job_id, generation)
    assert len(prepared) == Worker.LOCAL_ITEM_QUANTUM and not finished
    assert application.job(job_id)["state"] == "queued"
    next_id, next_generation = worker._claim_next()
    assert next_id == job_id and next_generation > generation
    await worker.execute(next_id, next_generation)
    assert len(prepared) == Worker.LOCAL_ITEM_QUANTUM + 1
    assert finished == [job_id]
