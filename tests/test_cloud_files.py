"""Cloud placeholders must not hydrate implicitly or hold up the rest of a scan."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from djlib.application import requests
from djlib.audio import catalog_inspection
from djlib.audio.file_identity import is_cloud_placeholder
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestCreate, RequestItem, RequestRefresh
from djlib.jobs import worker
from tests.conftest import execute


@pytest.mark.parametrize("flags,expected", [(0, False), (0x40000000, True), (0x8000, False)])
def test_cloud_flag_is_read_without_opening_file_contents(flags, expected):
    path = SimpleNamespace(stat=lambda: SimpleNamespace(st_flags=flags))
    assert is_cloud_placeholder(path) is expected
    # Platforms whose stat has no macOS flags continue normally.
    assert is_cloud_placeholder(SimpleNamespace(stat=lambda: SimpleNamespace())) is False


async def test_scan_indexes_local_audio_and_can_resume_after_cloud_download(
    application, audio_factory, monkeypatch
):
    cloud = audio_factory("cloud.wav", frequency=350, artist="Artist", title="Cloud Tune")
    audio_factory("local.wav", frequency=450, artist="Artist", title="Local Tune")
    offline = {cloud}
    real_labels = worker.labels

    def labels(path):
        assert path not in offline, "Reading tags would trigger a cloud download"
        return real_labels(path)

    monkeypatch.setattr(worker, "labels", labels)
    monkeypatch.setattr(worker, "is_cloud_placeholder", lambda path: path in offline)
    first = application.scan(str(cloud.parent), "cloud-first")
    result = await execute(application, first["job_id"])
    assert result["outcome"] == "completed_with_gaps"
    assert result["result"]["skipped_files"]["cloud_only_files"] == 1
    assert "Local Tune" in {t["title"] for t in application.library()["tracks"]}
    assert "Cloud Tune" not in {t["title"] for t in application.library()["tracks"]}

    offline.clear()
    second = application.scan(str(cloud.parent), "cloud-downloaded")
    resumed = await execute(application, second["job_id"])
    assert resumed["outcome"] == "complete"
    assert resumed["result"]["skipped_files"]["cloud_only_files"] == 0
    assert "Cloud Tune" in {t["title"] for t in application.library()["tracks"]}


def test_file_evicted_after_discovery_does_not_block_checksum_or_decoder(
    audio_factory, monkeypatch
):
    path = audio_factory("evicted.wav")
    monkeypatch.setattr(catalog_inspection, "is_cloud_placeholder", lambda _: True)

    def never_read(*args):
        pytest.fail("Cloud-only audio must not be opened for hashing or decoding")

    monkeypatch.setattr(catalog_inspection, "checksum", never_read)
    with pytest.raises(AppError) as error:
        catalog_inspection.inspect_catalog_audio(path, inspector=never_read, known=never_read)
    assert error.value.code == "SOURCE_CLOUD_ONLY"
    assert "Download it in Finder" in error.value.message


async def test_request_matching_does_not_download_a_previously_indexed_cloud_file(
    application, audio_factory, monkeypatch
):
    cloud = audio_factory("known.wav", frequency=380, artist="Artist", title="Known Tune")
    job = application.scan(str(cloud.parent), "known-before-eviction")
    await execute(application, job["job_id"])
    offline = {cloud}
    real_open = requests.os.open

    def local_open(path, *args, **kwargs):
        assert path not in offline, "Ownership checks must not open cloud-only audio"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(requests, "is_cloud_placeholder", lambda path: path in offline)
    monkeypatch.setattr(requests.os, "open", local_open)
    result = requests.create_request(
        application,
        RequestCreate(
            name="Cloud match",
            items=[RequestItem(artist="Artist", title="Known Tune")],
            idempotency_key="cloud-match",
        ),
    )
    item = result["items"][0]
    assert item["state"] == "unavailable" and item["accepted"] is None
    assert item["candidates"][0]["availability"] == "cloud_only"

    offline.clear()
    refreshed = requests.refresh_request(
        application, result["request_id"], RequestRefresh(revision=result["revision"])
    )
    assert refreshed["items"][0]["state"] == "satisfied"


@pytest.mark.parametrize("stage", ["authorize", "cloud_check", "tags"])
async def test_a_vanished_file_does_not_discard_other_downloaded_music(
    application, audio_factory, monkeypatch, stage
):
    vanished = audio_factory("a-vanished.wav", artist="Artist", title="Vanished Tune")
    audio_factory("z-local.wav", frequency=450, artist="Artist", title="Local Tune")
    authorize = application.workspace.authorize
    cloud_check = worker.is_cloud_placeholder
    labels = worker.labels

    def racing_authorize(value, **kwargs):
        if stage == "authorize" and Path(value) == vanished:
            vanished.unlink()
        return authorize(value, **kwargs)

    def racing_cloud_check(path):
        if stage == "cloud_check" and path == vanished:
            vanished.unlink()
        return cloud_check(path)

    def racing_labels(path):
        result = labels(path)
        if stage == "tags" and path == vanished:
            vanished.unlink()
        return result

    monkeypatch.setattr(application.workspace, "authorize", racing_authorize)
    monkeypatch.setattr(worker, "is_cloud_placeholder", racing_cloud_check)
    monkeypatch.setattr(worker, "labels", racing_labels)
    job = application.scan(str(vanished.parent), "vanish-" + stage)
    result = await execute(application, job["job_id"])
    assert result["state"] == "completed"
    assert result["outcome"] == "completed_with_gaps"
    assert result["result"]["skipped_files"]["unavailable_files"] == 1
    titles = {track["title"] for track in application.library()["tracks"]}
    assert "Local Tune" in titles and "Vanished Tune" not in titles


async def test_a_file_evicted_while_reading_tags_is_not_queued_for_decoding(
    application, audio_factory, monkeypatch
):
    evicted = audio_factory("evicted.wav", artist="Artist", title="Evicted Tune")
    offline = set()
    labels = worker.labels

    def evict_after_tags(path):
        result = labels(path)
        if path == evicted:
            offline.add(path)
        return result

    monkeypatch.setattr(worker, "labels", evict_after_tags)
    monkeypatch.setattr(worker, "is_cloud_placeholder", lambda path: path in offline)
    job = application.scan(str(evicted.parent), "eviction-during-tags")
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "completed_with_gaps"
    assert result["result"]["skipped_files"]["cloud_only_files"] == 1
    assert "Evicted Tune" not in {track["title"] for track in application.library()["tracks"]}


async def test_changed_tags_are_not_bound_to_different_file_bytes(
    application, audio_factory, monkeypatch
):
    changed = audio_factory("changed.wav", artist="Artist", title="Old Tags")
    labels = worker.labels

    def replace_after_tags(path):
        result = labels(path)
        if path == changed:
            path.write_bytes(b"a different file replaced the downloaded audio")
        return result

    monkeypatch.setattr(worker, "labels", replace_after_tags)
    job = application.scan(str(changed.parent), "changed-during-tags")
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "completed_with_gaps"
    assert result["result"]["skipped_files"]["unavailable_files"] == 1
    assert "Old Tags" not in {track["title"] for track in application.library()["tracks"]}


@pytest.mark.parametrize("stage", ["authorize", "cloud_check", "walk"])
def test_permission_errors_are_not_reported_as_a_complete_or_skipped_scan(
    application, audio_factory, monkeypatch, stage
):
    denied = audio_factory("denied.wav")
    authorize = application.workspace.authorize

    def denied_authorize(value, **kwargs):
        if Path(value) == denied:
            # Workspace.authorize wraps resolve errors; a permissions failure must
            # not be confused with a file that merely disappeared during scanning.
            raise AppError("FILE_UNAVAILABLE", "Permission denied") from PermissionError(value)
        return authorize(value, **kwargs)

    def denied_cloud_check(path):
        if path == denied:
            raise PermissionError(str(path))
        return False

    def denied_walk(root, *, onerror):
        onerror(PermissionError(str(root)))
        yield  # Keep the error deferred until the directory iterator is consumed.

    if stage == "authorize":
        monkeypatch.setattr(application.workspace, "authorize", denied_authorize)
    elif stage == "cloud_check":
        monkeypatch.setattr(worker, "is_cloud_placeholder", denied_cloud_check)
    else:
        monkeypatch.setattr(worker.os, "walk", denied_walk)
    scanner = worker.Worker(application)
    try:
        with pytest.raises(AppError if stage == "authorize" else PermissionError):
            scanner.discover(str(denied.parent))
    finally:
        scanner._pool.shutdown()


async def test_app_notification_sounds_are_skipped_before_cloud_or_tag_access(
    application, audio_factory, tmp_path, monkeypatch
):
    own_stem = audio_factory(
        "Assets/Artist - Own Stem.wav", frequency=615, artist="Artist", title="Own Stem"
    )
    bundled = tmp_path / "Example.app" / "Contents" / "Resources" / "finished.wav"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(own_stem.read_bytes())
    application.workspace.add_roots([tmp_path])
    real_cloud_check, real_labels = worker.is_cloud_placeholder, worker.labels

    def cloud_check(path):
        assert path != bundled, "App resources must not be opened or hydrated"
        return real_cloud_check(path)

    def labels(path):
        assert path != bundled, "App resources must not be read for tags"
        return real_labels(path)

    monkeypatch.setattr(worker, "is_cloud_placeholder", cloud_check)
    monkeypatch.setattr(worker, "labels", labels)
    job = application.scan(str(tmp_path), "skip-app-resources")
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete"
    assert result["result"]["skipped_files"]["software_assets"] == 1
    paths = {
        location["path"]
        for track in application.library(limit=100)["tracks"]
        for location in track["locations"]
    }
    assert str(own_stem) in paths and str(bundled) not in paths


async def test_scan_can_recover_filename_labels_from_a_full_filename_title_tag(
    application, audio_factory, monkeypatch
):
    stem = "SOPHIE - NOTHING MORE TO SAY (DUB)"
    source = audio_factory(stem + ".wav", frequency=690, title=stem)
    real_name_labels = worker.file_name_labels

    # Model a catalog created before the fallback handled title-only filename tags.
    monkeypatch.setattr(worker, "file_name_labels", lambda stem: ("", ""))
    first = application.scan(str(source.parent), "full-stem-before")
    assert (await execute(application, first["job_id"]))["outcome"] == "complete"
    old = next(t for t in application.library(limit=100)["tracks"] if t["path"] == str(source))
    assert old["artist"] == "Unknown artist" and old["title"] == stem

    monkeypatch.setattr(worker, "file_name_labels", real_name_labels)
    second = application.scan(str(source.parent), "full-stem-rescan")
    assert (await execute(application, second["job_id"]))["outcome"] == "complete"
    new = next(t for t in application.library(limit=100)["tracks"] if t["path"] == str(source))
    assert (new["artist"], new["title"]) == ("SOPHIE", "NOTHING MORE TO SAY (DUB)")
    assert new["recording_id"] == old["recording_id"]
    assert new["sha256"] == old["sha256"]
    assert new["identity_evidence"]["method"] == "provisional_bytes"
    assert new["identity_evidence"]["labels_source"] == "file_name"
    assert new["identity_evidence"]["labels_are_identity"] is False
    assert new["properties"]["artist"] == "" and new["properties"]["title"] == stem
    request = requests.create_request(
        application,
        RequestCreate(
            name="Dub",
            items=[RequestItem(artist="SOPHIE", title="NOTHING MORE TO SAY", version="DUB")],
            idempotency_key="full-stem-exact-dub",
        ),
    )
    item = request["items"][0]
    assert item["state"] == "satisfied"
    assert item["accepted"]["identity_match"] == "file_name_labels"


@pytest.mark.parametrize(
    "filename,artist,title",
    [
        ("Filename Artist - Filename Title.wav", "", "Different Embedded Title"),
        ("Filename Artist - Filename Title.wav", "Real Artist", "Real Title"),
        ("Filename Artist - Filename Title.wav", "Unknown artist", "Filename Title"),
        ("Plain Title.wav", "", "Plain Title"),
    ],
)
async def test_filename_fallback_preserves_actual_or_conflicting_tags(
    application, audio_factory, filename, artist, title
):
    source = audio_factory(filename, frequency=730, artist=artist, title=title)
    job = application.scan(str(source.parent), "preserve-title-only")
    result = await execute(application, job["job_id"])
    assert result["outcome"] == "complete"
    track = next(t for t in application.library(limit=100)["tracks"] if t["path"] == str(source))
    assert (track["artist"], track["title"]) == (artist or "Unknown artist", title)
