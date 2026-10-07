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


async def test_scan_writes_and_reads_items_in_batches(application, audio_factory, monkeypatch):
    """Large libraries: no 10,000-file cap, and item rows are handled a batch at a time."""
    paths = [audio_factory(f"batch-{n}.wav", frequency=200 + n * 10) for n in range(6)]
    monkeypatch.setattr(Worker, "SCAN_BATCH", 2)
    job = application.scan(str(paths[0].parent), "batched")
    result = await execute(application, job["job_id"])
    assert result["counts"] == {"succeeded": 7}  # fixture tone + six
    assert result["result"]["discovered_files"] == 7
    with application.db.transaction() as session:
        ids = list(session.scalars(select(JobItem.id).where(JobItem.job_id == job["job_id"])))
    paths_by_item = Worker(application)._local_paths("scan", ids)
    assert len(paths_by_item) == 7 and set(paths_by_item.values()) >= {str(p) for p in paths}


def test_scan_cap_bounds_memory_with_a_clear_message(application, audio_factory, monkeypatch):
    paths = [audio_factory(f"cap-{n}.wav", frequency=200 + n * 10) for n in range(3)]
    assert Worker.SCAN_FILE_LIMIT >= 50_000
    monkeypatch.setattr(Worker, "SCAN_FILE_LIMIT", 2)
    with pytest.raises(AppError) as error:
        Worker(application).discover(str(paths[0].parent))
    assert error.value.code == "ITEM_LIMIT"
    assert error.value.message == (
        "A scan is limited to 2 music files; scan its subfolders one at a time."
    )


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


OFFICIAL = "https://soundcloud.com/phoenix/lasso"
LABEL = "https://soundcloud.com/glassnotemusic/lasso"


def lasso_download(application, key, alternates=(), url=OFFICIAL):
    from djlib.domain.contracts import DownloadRequest, SourceTrack

    track = SourceTrack(url=url, artist="Phoenix", title="Lasso", alternates=list(alternates))
    return application.download(
        DownloadRequest(name="Tonight — downloads", idempotency_key=key, tracks=[track])
    )


def fake_provider(monkeypatch, source, failures):
    calls = []

    async def fake_download(url, destination):
        calls.append((url, destination.name))
        if url in failures:
            raise failures[url]
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "audio.wav"
        shutil.copyfile(source, path)
        return path, {"kind": "test_download", "source_url": url}

    monkeypatch.setattr("djlib.jobs.worker.download", fake_download)
    return calls


async def test_download_falls_back_to_another_upload_when_one_is_gone(
    application, audio_factory, monkeypatch
):
    gone = AppError("SOURCE_UNAVAILABLE", "The public recording is unavailable here.", 502)
    calls = fake_provider(monkeypatch, audio_factory("lasso.wav", frequency=480), {OFFICIAL: gone})
    job = lasso_download(application, "fallback", [OFFICIAL, LABEL, "https://youtu.be/lasso"])
    result = await execute(application, job["job_id"])

    assert result["outcome"] == "complete"
    [item] = application.items(job["job_id"])["items"]
    # Each upload downloads into its own folder; later alternates are never touched.
    assert calls == [(OFFICIAL, item["item_id"]), (LABEL, f"{item['item_id']}-1")]
    assert item["result"]["source_url"] == LABEL
    assert item["result"]["tried_urls"] == [OFFICIAL, LABEL]
    track = application.collection(result["result"]["collection_id"])["tracks"][0]
    assert track["properties"]["provenance"]["source_url"] == LABEL
    assert track["properties"]["provenance"]["tried_urls"] == [OFFICIAL, LABEL]


@pytest.mark.parametrize(
    ("error", "tried"),
    [
        # A refused stream may work on a retry of the same upload: no fallback.
        (AppError("SOURCE_FAILED", "Refused.", 502, True), [OFFICIAL]),
        (AppError("SOURCE_UNAVAILABLE", "Gone.", 502), [OFFICIAL, LABEL]),
    ],
)
async def test_download_failures_record_every_upload_tried(
    application, audio_factory, monkeypatch, error, tried
):
    calls = fake_provider(monkeypatch, audio_factory(), {OFFICIAL: error, LABEL: error})
    job = lasso_download(application, "failing", [LABEL])
    result = await execute(application, job["job_id"])

    assert result["counts"] == {"failed": 1}
    [item] = application.items(job["job_id"])["items"]
    assert [url for url, _ in calls] == tried
    assert item["result"]["tried_urls"] == tried
    assert item["result"]["error"]["code"] == error.code
    assert item["result"]["error"]["retryable"] is error.retryable


def test_download_alternates_are_validated_and_optional(application):
    from pydantic import ValidationError

    from djlib.domain.contracts import SourceTrack

    with pytest.raises(ValidationError):
        SourceTrack(url=OFFICIAL, artist="A", title="T", alternates=[LABEL] * 4)
    with pytest.raises(AppError) as error:
        lasso_download(application, "elsewhere", ["https://evil.test/lasso"])
    assert error.value.code == "SOURCE_UNSUPPORTED"
    # Without alternates the stored request is what earlier releases stored.
    plain = lasso_download(application, "plain")
    assert "alternates" not in application.items(plain["job_id"])["items"][0]["input"]
    assert lasso_download(application, "plain")["job_id"] == plain["job_id"]


@pytest.mark.parametrize(
    ("requested", "tags"),
    [
        (("Phoenix", "Lasso (Original Mix)", ""), ("Phoenix", "Lasso")),
        (("Phoenix", "Lasso", "Original Mix"), ("Phoenix", "Lasso")),
        (("Phoenix", "Lasso", ""), ("Phoenix", "Lasso (Original Mix)")),
        (("Antdot & Maz (BR)", "Lasso", ""), ("Antdot, Maz", "Lasso")),
        (("Maz (BR), Antdot", "Lasso", ""), ("Antdot & Maz", "Lasso")),
        (("Phoenix feat. Ana", "Lasso", ""), ("Phoenix", "Lasso (feat. Ana)")),
        (("Phoenix", "Lasso", "Maz (BR) Remix"), ("Phoenix", "Lasso (Maz Remix)")),
        (("Phoenix", "Lasso (Extended Mix)", ""), ("Phoenix", "Lasso (Extended Mix)")),
        (("Phoenix", "Lasso", ""), ("Phoenix", "")),
        (("Phoenix", "Lasso", ""), ("", "Lasso")),
    ],
)
def test_equivalent_labels_are_not_a_metadata_conflict(requested, tags):
    from types import SimpleNamespace

    track = TrackInput(path="/x.mp3", artist=requested[0], title=requested[1], version=requested[2])
    file = SimpleNamespace(artist=tags[0], title=tags[1])
    assert Worker.identity_conflict(track, file) is False


@pytest.mark.parametrize(
    ("requested", "tags"),
    [
        (("Phoenix", "Lasso (Two Door Cinema Club Remix)", ""), ("Phoenix", "Lasso")),
        (("Phoenix", "Lasso", ""), ("Phoenix", "Lasso (Two Door Cinema Club Remix)")),
        (("Phoenix", "Lasso", "Two Door Cinema Club Remix"), ("Phoenix", "Lasso")),
        (("Phoenix", "Lasso", "Extended Mix"), ("Phoenix", "Lasso")),
        (("Phoenix", "Lasso", ""), ("Phoenix", "Lasso (Live)")),
        (("Phoenix", "Lasso", ""), ("Glassnote Records", "Lasso")),
        (("Antdot & Maz", "Lasso", ""), ("Antdot", "Lasso")),
        (("Phoenix", "Lasso", ""), ("Phoenix", "Entertainment")),
    ],
)
def test_other_versions_and_artists_are_still_a_metadata_conflict(requested, tags):
    from types import SimpleNamespace

    track = TrackInput(path="/x.mp3", artist=requested[0], title=requested[1], version=requested[2])
    assert Worker.identity_conflict(track, SimpleNamespace(artist=tags[0], title=tags[1])) is True


async def test_download_tagged_without_original_mix_needs_no_review(
    application, audio_factory, monkeypatch
):
    """The live case: the label's MP3 is tagged "Lasso"; the tracklist said "(Original Mix)"."""
    from djlib.domain.contracts import DownloadRequest, SourceTrack

    tagged = audio_factory("glassnote.wav", frequency=490, artist="Phoenix", title="Lasso")
    fake_provider(monkeypatch, tagged, {})
    track = SourceTrack(url=LABEL, artist="Phoenix", title="Lasso (Original Mix)")
    job = application.download(
        DownloadRequest(name="Tonight — downloads", idempotency_key="tagged", tracks=[track])
    )
    result = await execute(application, job["job_id"])
    assert (result["state"], result["outcome"]) == ("completed", "complete")
    assert not application.reviews(job["job_id"])["reviews"]
