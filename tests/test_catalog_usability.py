"""Everyday catalog use: findable tracks, equivalent mix labels and safe scans."""

import os
import struct
from pathlib import Path

import pytest

from djlib.application import requests
from djlib.application.demo import run_demo
from djlib.audio.inspection import labels
from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestCreate, RequestItem
from djlib.exporting.handoff import device_preflight, playlist_label
from tests.conftest import execute


async def scan(application, path, key="scan"):
    job = application.scan(str(path), key)
    return await execute(application, job["job_id"])


async def build(application, path, title, version="", key="build"):
    plan = application.plan(
        CollectionRequest(
            name=key,
            tracks=[
                TrackInput(path=str(path), artist="Velvet Static", title=title, version=version)
            ],
        )
    )
    job = application.start(StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key=key))
    return await execute(application, job["job_id"])


async def test_search_ignores_case_accents_and_word_order(application, audio_factory, tmp_path):
    audio_factory("a.wav", frequency=230, artist="Björk", title="Jóga")
    audio_factory("b.wav", frequency=330, artist="RØYKSOPP", title="Eple")
    audio_factory("c.wav", frequency=430, artist="Velvet Static", title="Night Bus (Radio Edit)")
    finished = await scan(application, tmp_path / "music")
    assert finished["outcome"] == "complete"

    def titles(query):
        return [track["title"] for track in application.library(query)["tracks"]]

    assert titles("bjork") == titles("JOGA") == ["Jóga"]
    assert titles("royksopp") == ["Eple"]
    assert titles("radio velvet") == ["Night Bus (Radio Edit)"]
    assert titles("radio bjork") == []

    # Artist/title order, stable across keyset pages.
    expected = ["Björk", "RØYKSOPP", "Unknown artist", "Velvet Static"]
    assert [t["artist"] for t in application.library()["tracks"]] == expected
    paged, after = [], None
    while True:
        page = application.library(limit=1, after=after)
        paged += [t["artist"] for t in page["tracks"]]
        if not (after := page["next_cursor"]):
            break
    assert paged == expected


async def test_scan_skips_working_copies_and_links_outside_allowed_folders(
    application, audio_factory, tmp_path
):
    working = application.workspace.exports / "job_old" / "copy.wav"
    working.parent.mkdir(parents=True)
    working.write_bytes(audio_factory("source.wav", frequency=550).read_bytes())
    outside = tmp_path / "elsewhere" / "private.wav"
    outside.parent.mkdir()
    outside.write_bytes(audio_factory("other.wav", frequency=660).read_bytes())
    try:
        os.symlink(outside, tmp_path / "music" / "link.wav")
    except OSError:
        pytest.skip("Symlinks need extra privileges on this platform")
    linked = await scan(application, tmp_path / "music", key="linked")
    assert linked["outcome"] == "complete"
    assert linked["result"]["skipped_files"]["outside_allowed_folders"] == 1

    # With the parent allowed, the workspace's own exports sit inside the scanned tree.
    application.workspace.add_roots([tmp_path])
    finished = await scan(application, tmp_path, key="parent")
    assert finished["outcome"] == "complete"
    assert finished["result"]["skipped_files"]["workspace_files"] == 1
    paths = {t["path"] for t in application.library(limit=100)["tracks"]}
    assert not any(Path(path).is_relative_to(application.workspace.root) for path in paths)


def test_wav_info_chunk_supplies_labels(audio_factory):
    path = audio_factory("info.wav")

    def field(key, text):
        data = text.encode() + b"\0"
        data += b"\0" * (len(data) & 1)
        return key + struct.pack("<I", len(data)) + data

    body = b"INFO" + field(b"IART", "Jeff Example") + field(b"INAM", "The Bells")
    raw = path.read_bytes() + b"LIST" + struct.pack("<I", len(body)) + body
    path.write_bytes(raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:])
    assert labels(path) == ("Jeff Example", "The Bells")


@pytest.mark.parametrize(
    ("catalog", "requested", "state"),
    [
        (("Rain", "Extended Mix"), ("Rain (Extended Mix)", ""), "satisfied"),
        (("Rain (Extended Mix)", ""), ("Rain", "Extended Mix"), "satisfied"),
        (("Rain [Extended Mix]", ""), ("Rain", "Extended Mix"), "satisfied"),
        (("Rain (Radio Edit)", ""), ("Rain", "Extended Mix"), "missing"),
    ],
)
async def test_requests_match_mix_names_written_in_the_title(
    application, audio_factory, catalog, requested, state
):
    source = audio_factory("rain.wav", frequency=770)
    assert (await build(application, source, *catalog))["outcome"] == "complete"
    ledger = requests.create_request(
        application,
        RequestCreate(
            name="Rain",
            idempotency_key="rain",
            items=[RequestItem(artist="Velvet Static", title=requested[0], version=requested[1])],
        ),
    )
    item = ledger["items"][0]
    assert item["state"] == state
    if state == "satisfied":
        assert item["accepted"]["identity_match"] == "equivalent_labels"


async def test_same_bytes_with_equivalent_labels_reuse_one_recording(application, audio_factory):
    source = audio_factory("rain.wav", frequency=880)
    first = await build(application, source, "Rain (Extended Mix)", key="first")
    second = await build(application, source, "Rain", "Extended Mix", key="second")
    assert first["outcome"] == second["outcome"] == "complete"
    one = application.collection(first["result"]["collection_id"])["tracks"][0]
    two = application.collection(second["result"]["collection_id"])["tracks"][0]
    assert one["recording_id"] == two["recording_id"]


async def test_same_bytes_with_a_different_version_still_conflict(application, audio_factory):
    source = audio_factory("rain.wav", frequency=990)
    await build(application, source, "Rain (Extended Mix)", key="first")
    second = await build(application, source, "Rain", "Radio Edit", key="second")
    assert second["counts"] == {"failed": 1}
    item = application.items(second["job_id"])["items"][0]
    assert item["result"]["error"]["code"] == "EXISTING_IDENTITY_CONFLICT"
    assert "organize collection" in item["result"]["error"]["message"]


def test_demo_refuses_a_real_library(application):
    with pytest.raises(AppError) as error:
        run_demo(application.workspace)
    assert error.value.code == "DEMO_WORKSPACE_IN_USE"


def test_listing_errors_are_specific(application, tmp_path):
    job = application.scan(str(tmp_path / "music"), "noop")
    with pytest.raises(AppError) as error:
        application.items(job["job_id"], state="bogus")
    assert error.value.code == "INPUT_INVALID"
    with pytest.raises(AppError) as missing:
        requests.get_request(application, "request_missing")
    assert missing.value.message == "Request list was not found."


def test_playlist_labels_keep_versions():
    assert playlist_label({"artist": "A", "title": "T", "version": "Dub"}) == "A - T (Dub)"
    assert playlist_label({"artist": "A", "title": "T", "version": ""}) == "A - T"


def test_usb_preflight_next_step_follows_the_check(tmp_path):
    assert "top folder" in device_preflight(str(tmp_path))["next_step"]
    anchor = Path(tmp_path.anchor)
    if anchor.is_mount():
        full = device_preflight(str(anchor), required_bytes=10**18)
        assert full["has_requested_space"] is False
        assert "Free space" in full["next_step"]


async def test_untagged_files_take_labels_from_their_names(application, audio_factory, tmp_path):
    music = tmp_path / "music"
    unnamed = audio_factory("track07.wav", frequency=812)
    first = await scan(application, music, key="first")
    assert first["outcome"] == "complete"
    row = next(
        t
        for t in application.library(limit=100)["tracks"]
        if t["sha256"] and "track07" in t["title"]
    )
    assert row["artist"] == "Unknown artist"

    # Renaming the same bytes to "Artist - Title" relabels the provisional recording on rescan.
    named = unnamed.rename(music / "03 - Velvet Static - Night Bus (Dub).wav")
    second = await scan(application, music, key="second")
    assert second["outcome"] == "complete"
    rows = [t for t in application.library(limit=100)["tracks"] if t["sha256"] == row["sha256"]]
    assert [(t["artist"], t["title"]) for t in rows] == [("Velvet Static", "Night Bus (Dub)")]
    assert rows[0]["recording_id"] == row["recording_id"]  # same byte identity
    assert rows[0]["identity_evidence"]["method"] == "provisional_bytes"
    assert rows[0]["identity_evidence"]["labels_source"] == "file_name"

    ledger = requests.create_request(
        application,
        RequestCreate(
            name="Dub",
            idempotency_key="dub",
            items=[RequestItem(artist="Velvet Static", title="Night Bus", version="Dub")],
        ),
    )
    item = ledger["items"][0]
    assert item["state"] == "satisfied"
    assert item["accepted"]["identity_match"] == "file_name_labels"
    assert item["accepted"]["path"] == str(named)
