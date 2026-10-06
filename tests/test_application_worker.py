"""End-to-end use cases and failure injection against the real SQLite worker."""

import asyncio
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from sqlalchemy import func, select

from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.jobs.worker import Worker
from djlib.persistence.models import AssetRevision, Job, JobItem, Membership, Operation
from tests.conftest import execute, submit_collection


def test_submission_idempotency_and_conflict(application, audio_factory):
    path = audio_factory()
    plan = application.plan(
        CollectionRequest(
            name="Set", tracks=[TrackInput(path=str(path), artist="Artist", title="Track")]
        )
    )
    request = StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key="stable")
    first = application.start(request)
    assert application.start(request)["job_id"] == first["job_id"]
    with pytest.raises(AppError) as error:
        application.scan(str(path.parent), "stable")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(AppError) as error:
        application.start(request.model_copy(update={"revision": 2}))
    assert error.value.code == "PLAN_STALE"


async def test_local_collection_export_roundtrip(application, audio_factory):
    paths = [audio_factory(f"track-{n}.wav", frequency=220 + n * 20) for n in range(3)]
    job = submit_collection(application, paths)
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete" and result["counts"] == {"succeeded": 3}
    collection = application.collection(result["result"]["collection_id"])
    assert [t["title"] for t in collection["tracks"]] == [p.stem for p in paths]
    assert all(not t["managed"] for t in collection["tracks"])
    export = application.export(collection["collection_id"], "export")
    assert application.export(collection["collection_id"], "export")["job_id"] == export["job_id"]
    exported = await execute(application, export["job_id"])
    assert exported["outcome"] == "complete"
    manifest = json.loads(Path(exported["result"]["manifest_path"]).read_text())
    assert manifest["device_state"] == "not_exported"
    xml = ET.parse(exported["result"]["rekordbox_xml_path"])
    assert len(xml.findall("COLLECTION/TRACK")) == 3
    assert application.library("TEST")["tracks"]


async def test_duplicate_bytes_and_membership_reused(application, audio_factory):
    path = audio_factory()
    first = submit_collection(application, [path, path], profile="archive")
    result = await execute(application, first["job_id"])
    assert result["counts"] == {"succeeded": 2}
    collection = application.collection(result["result"]["collection_id"])
    assert len(collection["tracks"]) == 1 and collection["tracks"][0]["managed"]
    assert len(list(application.workspace.managed.iterdir())) == 1
    second = submit_collection(application, [path], key="second")
    await execute(application, second["job_id"])
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AssetRevision)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 2
    assert application.items(second["job_id"])["items"][0]["result"]["reused"]


async def test_changed_file_export_rejected(application, audio_factory):
    path = audio_factory()
    job = submit_collection(application, [path])
    result = await execute(application, job["job_id"])
    export = application.export(result["result"]["collection_id"], "export")
    audio_factory(frequency=440)
    with pytest.raises(AppError) as error:
        await execute(application, export["job_id"])
    assert error.value.code == "RECONCILIATION_REQUIRED"
    assert not (application.workspace.exports / export["job_id"]).exists()


@pytest.mark.parametrize("choice", ["accept_requested", "use_file_metadata", "skip"])
async def test_review_choices_and_stale_resolution(application, audio_factory, choice):
    path = audio_factory(artist="Actual artist", title="Actual track (Radio Edit)")
    job = submit_collection(application, [path])
    result = await execute(application, job["job_id"])
    assert result["state"] == "needs_attention"
    review = application.reviews(job["job_id"])["reviews"][0]
    assert application.resolve(review["review_id"], 1, choice)["state"] == "queued"
    with pytest.raises(AppError) as error:
        application.resolve(review["review_id"], 1, choice)
    assert error.value.code == "REVIEW_STALE"
    result = await execute(application, job["job_id"])
    assert result["state"] == "completed"
    tracks = application.collection(result["result"]["collection_id"])["tracks"]
    if choice == "skip":
        assert not tracks and result["outcome"] == "completed_with_gaps"
    else:
        assert tracks[0]["artist"] == (
            "Actual artist" if choice == "use_file_metadata" else "Test Artist"
        )


async def test_partial_failure_and_retry_preserve_successes(application, audio_factory):
    good, broken = audio_factory(), audio_factory("broken.wav", frequency=440)
    broken.write_bytes(b"broken")
    job = submit_collection(application, [good, broken])
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "completed_with_gaps"
    assert result["counts"] == {"succeeded": 1, "failed": 1}
    audio_factory("broken.wav", frequency=440)
    application.control(job["job_id"], "retry")
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete"
    assert result["counts"] == {"succeeded": 2}
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 3


async def test_cancel_pending_preserves_terminal_items(application, audio_factory):
    job = submit_collection(application, [audio_factory()])
    application.control(job["job_id"], "pause")
    assert application.job(job["job_id"])["state"] == "paused"
    application.control(job["job_id"], "resume")
    assert application.job(job["job_id"])["state"] == "queued"
    cancelled = application.control(job["job_id"], "cancel")
    assert cancelled["state"] == "cancelled" and cancelled["counts"] == {"cancelled": 1}
    with pytest.raises(AppError) as error:
        application.control(job["job_id"], "resume")
    assert error.value.code == "JOB_TERMINAL"


async def test_cancel_closes_reviews(application, audio_factory):
    job = submit_collection(application, [audio_factory(artist="Other", title="Other")])
    await execute(application, job["job_id"])
    application.control(job["job_id"], "cancel")
    assert not application.reviews()["reviews"]


async def test_restart_recovers_promoted_bytes_without_duplicate(application, audio_factory):
    path = audio_factory()
    job = submit_collection(application, [path], profile="archive")
    from djlib.audio.inspection import checksum

    destination = application.workspace.managed / f"{checksum(path)}.wav"
    shutil.copyfile(path, destination)
    with application.db.transaction() as session:
        value = session.get(Job, job["job_id"])
        value.state, value.generation = "running", 1
        item = session.scalar(select(JobItem).where(JobItem.job_id == value.id))
        item.state = "running"
    Worker(application).recover()
    assert application.job(job["job_id"])["state"] == "queued"
    assert application.items(job["job_id"])["items"][0]["state"] == "pending"
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete"
    assert len(list(application.workspace.managed.iterdir())) == 1


async def test_cancel_during_inspection_cannot_commit(application, audio_factory, monkeypatch):
    job = submit_collection(application, [audio_factory()])
    import djlib.jobs.worker as module

    original = module.inspect_audio

    def cancel(path):
        application.control(job["job_id"], "cancel")
        return original(path)

    monkeypatch.setattr(module, "inspect_audio", cancel)
    result = await execute(application, job["job_id"])
    assert result["state"] == "cancelled"
    assert not application.library()["tracks"]


async def test_scan_ignores_non_audio_and_pages_items(application, audio_factory):
    paths = [audio_factory(f"scan-{n}.wav", frequency=220 + n * 10) for n in range(7)]
    (paths[0].parent / "notes.txt").write_text("not music")
    job = application.scan(str(paths[0].parent), "scan")
    result = await execute(application, job["job_id"])
    assert result["counts"] == {"succeeded": 8}  # fixture tone + seven selections
    first = application.items(job["job_id"], limit=3)
    second = application.items(job["job_id"], limit=3, after=first["next_cursor"])
    assert set(i["item_id"] for i in first["items"]).isdisjoint(
        i["item_id"] for i in second["items"]
    )
    assert application.events(job["job_id"], limit=2)["next_cursor"] is not None


async def test_resolving_review_while_other_item_runs(application, audio_factory, monkeypatch):
    """A resolved item must not be stranded by the worker's original pending snapshot."""
    paths = [
        audio_factory("a.wav", artist="Other", title="Other"),
        audio_factory("b.wav", frequency=440),
    ]
    job = submit_collection(application, paths)
    original = Worker.ingest

    async def resolve_during_next_item(self, job_id, generation, item_id):
        if application.reviews()["reviews"]:
            review = application.reviews()["reviews"][0]
            application.resolve(review["review_id"], 1, "accept_requested")
        await original(self, job_id, generation, item_id)

    monkeypatch.setattr(Worker, "ingest", resolve_during_next_item)
    task = asyncio.create_task(Worker(application).run())
    try:
        async with asyncio.timeout(3):
            while application.job(job["job_id"])["state"] != "completed":
                await asyncio.sleep(0.01)
        result = application.job(job["job_id"])
        assert result["counts"] == {"succeeded": 2}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_large_batch_reuse_and_cursor_completeness(application, audio_factory):
    paths = [audio_factory(f"batch-{n}.wav", frequency=200 + n * 15) for n in range(20)]
    job = submit_collection(application, paths * 10, profile="archive", key="batch-200")
    result = await execute(application, job["job_id"])
    assert result["counts"] == {"succeeded": 200} and result["outcome"] == "complete"
    assert len(list(application.workspace.managed.iterdir())) == 20
    assert len(application.collection(result["result"]["collection_id"])["tracks"]) == 20
    after, positions = -1, []
    while True:
        page = application.items(job["job_id"], after=after, limit=31)
        positions.extend(item["position"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]
    assert positions == list(range(200))


@pytest.mark.parametrize("suffix", [".flac", ".mp3"])
async def test_download_tags_only_managed_copy_and_records_final_hash(
    application, audio_factory, monkeypatch, suffix
):
    import subprocess

    from djlib.audio.inspection import checksum, labels
    from djlib.domain.contracts import DownloadRequest, SourceTrack

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    source = audio_factory().with_suffix(suffix)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source.with_suffix(".wav")), str(source)],
        check=True,
        timeout=20,
    )
    acquired = {}

    async def fake_download(url, destination):
        destination.mkdir(parents=True)
        path = destination / f"audio{suffix}"
        shutil.copyfile(source, path)
        acquired.update(path=path, sha256=checksum(path))
        return path, {"kind": "test_download", "source_url": url, "source_quality": "unverified"}

    monkeypatch.setattr("djlib.jobs.worker.download", fake_download)
    job = application.download(
        DownloadRequest(
            name="Tagged downloads",
            idempotency_key="tagged-download",
            tracks=[
                SourceTrack(
                    url="https://youtu.be/test",
                    artist="Selected artist",
                    title="Track",
                    version="Dub",
                )
            ],
        )
    )
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete"
    item = application.items(job["job_id"])["items"][0]
    from pathlib import Path

    managed = Path(item["result"]["path"])
    assert labels(managed) == ("Selected artist", "Track (Dub)")
    assert checksum(acquired["path"]) == acquired["sha256"]
    assert labels(acquired["path"]) == ("", "")
    track = application.collection(result["result"]["collection_id"])["tracks"][0]
    assert track["sha256"] == checksum(managed) != acquired["sha256"]
    assert track["properties"]["provenance"]["acquired_sha256"] == acquired["sha256"]
