"""Original-tone request ledgers preserve uncertainty, exact versions, and CAS evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from djlib.application import requests
from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import (
    RequestCreate,
    RequestItem,
    RequestRefresh,
    RequestResolution,
)
from djlib.persistence.models import Job
from djlib.persistence.request_models import RequestLedger
from tests.conftest import execute


@pytest.fixture
def ledger_app(application):
    # Use the packaged migrations, including the real persistent request tables.
    return application


async def owned(app, factory, *, name="owned", artist="Original Artist", version="", frequency=440):
    source = factory(f"{name}.wav", frequency=frequency)
    plan = app.plan(
        CollectionRequest(
            name=name,
            tracks=[
                TrackInput(
                    path=str(source),
                    artist=artist,
                    title="Original Tone",
                    version=version,
                )
            ],
        )
    )
    job = app.start(StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key=name))
    completed = await execute(app, job["job_id"])
    assert completed["outcome"] == "complete"
    return app.collection(completed["result"]["collection_id"])["tracks"][0]


def named(**changes):
    return RequestItem(**{"artist": "Original Artist", "title": "Original Tone", **changes})


def create(app, items, key="songs"):
    return requests.create_request(
        app, RequestCreate(name="Tonight requests", items=items, idempotency_key=key)
    )


def resolve(app, ledger, **changes):
    return requests.resolve_request(
        app,
        ledger["request_id"],
        ledger["items"][0]["item_id"],
        RequestResolution(revision=ledger["revision"], **changes),
    )


async def test_owned_exact_reuse_and_duplicate_rows_are_persistent_without_jobs(
    ledger_app, audio_factory, monkeypatch
):
    track = await owned(ledger_app, audio_factory)
    checked = []
    verify = requests._Verification._location

    def count_reads(self, path, sha256):
        checked.append(path)
        return verify(self, path, sha256)

    def forbidden_submit(*args, **kwargs):
        pytest.fail("A song ledger must never start acquisition or other jobs")

    monkeypatch.setattr(requests._Verification, "_location", count_reads)
    monkeypatch.setattr(ledger_app, "submit", forbidden_submit)
    result = create(
        ledger_app, [named(), named(artist=" ORIGINAL artist "), named(title="Missing tone")]
    )
    assert result["total_items"] == 3
    assert result["counts"]["satisfied"] == 2
    assert result["counts"]["missing"] == 1
    first, duplicate, missing = result["items"]
    assert first["accepted"]["recording_id"] == track["recording_id"]
    assert first["accepted"]["asset_revision_id"] == track["asset_revision_id"]
    assert first["accepted"]["sha256"] == track["sha256"]
    assert first["accepted"]["availability"] == "verified"
    assert first["accepted"]["basis"] == "exact_catalog_labels"
    assert duplicate["duplicate_of"] == first["item_id"]
    assert missing["accepted"] is None
    assert checked == [track["path"]]
    assert result["unique_satisfied_recording_ids"] == [track["recording_id"]]
    assert result["catalog_snapshot_only"] is True
    assert result["automatic_acquisition"] is result["hardware_verified"] is False
    same = create(
        ledger_app, [named(), named(artist=" ORIGINAL artist "), named(title="Missing tone")]
    )
    assert same == result
    assert checked == [track["path"]]
    with ledger_app.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(RequestLedger)) == 1
        assert (
            session.scalar(select(func.count()).select_from(Job)) == 1
        )  # original catalog job only


async def test_different_version_is_only_a_candidate_and_cannot_be_selected(
    ledger_app, audio_factory
):
    remix = await owned(ledger_app, audio_factory, version="Night remix")
    result = create(ledger_app, [named()])
    item = result["items"][0]
    assert item["state"] == "missing"
    assert item["accepted"] is None
    assert item["candidates"][0]["identity_match"] == "different_version"
    assert item["candidates"][0]["availability"] == "not_checked"
    with pytest.raises(AppError) as error:
        resolve(
            ledger_app,
            result,
            action="satisfy",
            recording_id=remix["recording_id"],
            notes="Attempted substitute",
        )
    assert error.value.code == "REQUEST_IDENTITY_CONFLICT"
    assert requests.get_request(ledger_app, result["request_id"])["revision"] == 1


async def test_multiple_exact_byte_revisions_require_explicit_choice(ledger_app, audio_factory):
    first = await owned(ledger_app, audio_factory, name="first", frequency=220)
    second = await owned(ledger_app, audio_factory, name="second", frequency=330)
    assert first["recording_id"] == second["recording_id"]
    result = create(ledger_app, [named()])
    assert result["items"][0]["state"] == "ambiguous"
    assert result["items"][0]["accepted"] is None
    assert len(result["items"][0]["candidates"]) == 2
    with pytest.raises(AppError) as error:
        resolve(
            ledger_app,
            result,
            action="satisfy",
            recording_id=second["recording_id"],
            notes="Choose a recording only",
        )
    assert error.value.code == "REQUEST_RECORDING_UNAVAILABLE"
    selected = resolve(
        ledger_app,
        result,
        action="satisfy",
        recording_id=second["recording_id"],
        asset_revision_id=second["asset_revision_id"],
        notes="Explicitly selected the second original tone revision",
    )
    assert selected["revision"] == 2
    assert selected["items"][0]["accepted"]["asset_revision_id"] == second["asset_revision_id"]
    assert selected["items"][0]["accepted"]["basis"] == "operator_selection"
    refreshed = requests.refresh_request(
        ledger_app, selected["request_id"], RequestRefresh(revision=2)
    )
    assert refreshed["items"][0]["accepted"]["asset_revision_id"] == second["asset_revision_id"]


def test_unknown_timestamp_evidence_survives_atomic_missing_reports(ledger_app):
    unknown = RequestItem(
        kind="unknown",
        label="Unknown ID / crowd noise",
        timestamp="01:02:03.450",
        source_url="https://www.youtube.com/watch?v=example&t=3723",
        notes="Published chapter says ID — ID",
    )
    result = create(ledger_app, [unknown, named(title="Unidentified named song")])
    assert result["items"][0]["state"] == "unknown"
    assert result["items"][0]["candidates"] == []
    first = requests.export_missing_report(ledger_app, result["request_id"], 1)
    second = requests.export_missing_report(ledger_app, result["request_id"], 1)
    assert first["report_path"] != second["report_path"]
    path = Path(first["report_path"])
    assert path.is_relative_to(ledger_app.workspace.exports)
    report = json.loads(path.read_text())
    assert report["unresolved_items"] == 2
    assert report["items"][0]["input"] == unknown.model_dump()
    assert report["items"][0]["position"] == 1
    assert report["hardware_verified"] is False
    assert not list(path.parent.glob(".request-report-*"))


async def test_unknown_requires_explicit_identification_and_clear_preserves_evidence(
    ledger_app, audio_factory
):
    track = await owned(ledger_app, audio_factory)
    unknown = RequestItem(kind="unknown", label="Unidentified opener", timestamp="0:00")
    result = create(ledger_app, [unknown])
    selected = resolve(
        ledger_app,
        result,
        action="satisfy",
        recording_id=track["recording_id"],
        notes="Operator identified the original test tone",
    )
    assert selected["items"][0]["input"] == unknown.model_dump()
    assert selected["items"][0]["state"] == "satisfied"
    assert selected["items"][0]["accepted"]["identity_match"] == "operator_identified"
    cleared = resolve(ledger_app, selected, action="clear")
    assert cleared["items"][0]["state"] == "unknown"
    assert cleared["items"][0]["input"] == unknown.model_dump()
    assert cleared["items"][0]["accepted"] is None


async def test_selected_source_stays_unresolved_until_catalog_refresh(ledger_app, audio_factory):
    result = create(ledger_app, [named()])
    source_url = "https://soundcloud.com/original-artist/original-tone"
    selected = resolve(
        ledger_app,
        result,
        action="select_source",
        source_url=source_url,
        notes="Publisher metadata candidate, audio not yet acquired",
    )
    assert selected["items"][0]["state"] == "source_selected"
    assert selected["items"][0]["accepted"] is None
    assert selected["items"][0]["source_selection"]["verification"] == "selected_only_not_acquired"
    with ledger_app.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    track = await owned(ledger_app, audio_factory)
    assert (
        requests.get_request(ledger_app, result["request_id"])["items"][0]["state"]
        == "source_selected"
    )
    refreshed = requests.refresh_request(
        ledger_app, result["request_id"], RequestRefresh(revision=2)
    )
    assert refreshed["items"][0]["state"] == "satisfied"
    assert refreshed["items"][0]["accepted"]["recording_id"] == track["recording_id"]
    assert refreshed["items"][0]["source_selection"]["source_url"] == source_url
    report = requests.export_missing_report(ledger_app, result["request_id"], 3)
    assert json.loads(Path(report["report_path"]).read_text())["items"] == []


async def test_refresh_invalidates_changed_bytes_and_rejects_stale_evidence(
    ledger_app, audio_factory
):
    track = await owned(ledger_app, audio_factory)
    result = create(ledger_app, [named()])
    source = Path(track["path"])
    source.write_bytes(source.read_bytes() + b"synthetic corruption")
    assert (
        requests.get_request(ledger_app, result["request_id"])["items"][0]["state"] == "satisfied"
    )
    refreshed = requests.refresh_request(
        ledger_app, result["request_id"], RequestRefresh(revision=1)
    )
    assert refreshed["revision"] == 2
    assert refreshed["items"][0]["state"] == "unavailable"
    assert refreshed["items"][0]["accepted"] is None
    assert refreshed["items"][0]["candidates"][0]["availability"] == "changed"
    for operation in (
        lambda: requests.refresh_request(
            ledger_app, result["request_id"], RequestRefresh(revision=1)
        ),
        lambda: resolve(ledger_app, result, action="clear"),
        lambda: requests.export_missing_report(ledger_app, result["request_id"], 1),
    ):
        with pytest.raises(AppError) as error:
            operation()
        assert error.value.code == "REQUEST_STALE"


def test_semantic_idempotency_conflict_and_bounded_pages(ledger_app):
    result = create(ledger_app, [named(title=f"Tone {n}") for n in range(101)])
    assert len(result["items"]) == 100
    assert result["next_offset"] == 100
    last = requests.get_request(ledger_app, result["request_id"], offset=100, limit=1)
    assert len(last["items"]) == 1
    assert last["items"][0]["position"] == 101
    assert last["next_offset"] is None
    assert last["counts"]["missing"] == 101
    with pytest.raises(AppError) as error:
        create(ledger_app, [named(title="Different request")])
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    for changes in ({"limit": 101}, {"offset": -1}, {"limit": True}):
        with pytest.raises(AppError) as error:
            requests.get_request(ledger_app, result["request_id"], **changes)
        assert error.value.code == "REQUEST_PAGE_INVALID"


async def test_verification_bound_never_satisfies_unchecked_catalog_match(
    ledger_app, audio_factory, monkeypatch
):
    await owned(ledger_app, audio_factory)
    monkeypatch.setattr(requests, "MAX_VERIFY_BYTES", 1)
    result = create(ledger_app, [named()])
    assert result["items"][0]["state"] == "unavailable"
    assert result["items"][0]["accepted"] is None
    assert result["items"][0]["candidates"][0]["availability"] == "verification_limit"


async def test_targeted_refresh_can_advance_past_hash_budget(
    ledger_app, audio_factory, monkeypatch
):
    first = await owned(ledger_app, audio_factory, name="first", frequency=220)
    second = await owned(
        ledger_app, audio_factory, name="second", frequency=330, version="Night remix"
    )
    monkeypatch.setattr(requests, "MAX_VERIFY_BYTES", Path(first["path"]).stat().st_size)
    result = create(ledger_app, [named(), named(version="Night remix")])
    assert [item["state"] for item in result["items"]] == ["satisfied", "unavailable"]
    refreshed = requests.refresh_request(
        ledger_app,
        result["request_id"],
        RequestRefresh(revision=1, item_ids=[result["items"][1]["item_id"]]),
    )
    assert refreshed["items"][0] == result["items"][0]
    assert refreshed["items"][1]["accepted"]["asset_revision_id"] == second["asset_revision_id"]
    assert refreshed["counts"]["satisfied"] == 2
    with pytest.raises(AppError) as error:
        requests.refresh_request(
            ledger_app, result["request_id"], RequestRefresh(revision=2, item_ids=["missing-item"])
        )
    assert error.value.code == "NOT_FOUND"
    assert requests.get_request(ledger_app, result["request_id"])["revision"] == 2


async def test_symbol_only_artist_names_do_not_collapse_into_one_match(ledger_app, audio_factory):
    track = await owned(ledger_app, audio_factory, artist="!!!")
    result = create(ledger_app, [named(artist="???"), named(artist="!!!")])
    assert result["items"][0]["state"] == "missing"
    assert result["items"][0]["candidates"][0]["identity_match"] == "different_labels"
    assert result["items"][1]["accepted"]["recording_id"] == track["recording_id"]
    assert result["items"][1]["duplicate_of"] is None


def test_concurrent_same_key_submission_creates_one_durable_ledger(ledger_app, monkeypatch):
    rendezvous = Barrier(2)
    refresh = requests._refresh_item

    def synchronized_refresh(app, item, verification):
        result = refresh(app, item, verification)
        rendezvous.wait(timeout=5)
        return result

    monkeypatch.setattr(requests, "_refresh_item", synchronized_refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(ledger_app, [named()]), [1, 2]))
    assert results[0] == results[1]
    with ledger_app.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(RequestLedger)) == 1


def test_competing_same_revision_resolutions_preserve_one_winner(ledger_app, monkeypatch):
    result = create(ledger_app, [named()])
    rendezvous = Barrier(2)
    save = requests._save

    def synchronized_save(app, ledger):
        rendezvous.wait(timeout=5)
        return save(app, ledger)

    monkeypatch.setattr(requests, "_save", synchronized_save)

    def select_source(number):
        try:
            resolve(
                ledger_app,
                result,
                action="select_source",
                source_url=f"https://soundcloud.com/test/tone-{number}",
                notes=f"Operator {number}",
            )
        except AppError as error:
            return number, error.code
        return number, "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(select_source, [1, 2]))
    assert sorted(state for _, state in outcomes) == ["REQUEST_STALE", "saved"]
    winner = next(number for number, state in outcomes if state == "saved")
    current = requests.get_request(ledger_app, result["request_id"])
    assert current["revision"] == 2
    assert current["items"][0]["source_selection"]["notes"] == f"Operator {winner}"


def test_report_refuses_escape_through_existing_exports_symlink(ledger_app, tmp_path):
    result = create(ledger_app, [named()])
    outside = tmp_path / "outside-reports"
    outside.mkdir()
    (ledger_app.workspace.exports / "requests").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError) as error:
        requests.export_missing_report(ledger_app, result["request_id"], 1)
    assert error.value.code == "REPORT_PATH_INVALID"
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "value",
    [
        {"artist": "Artist"},
        {"artist": "Artist", "title": "Title", "download": True},
        {"artist": "Artist", "title": "Title", "version": 5},
        {"artist": " ", "title": "Title"},
        {"kind": "unknown", "label": "ID"},
        {"kind": "unknown", "label": "ID", "timestamp": "1:02", "artist": "Guess"},
        {"kind": "unknown", "label": "ID", "source_url": "file:///private/music"},
    ],
)
def test_request_items_are_strict_and_unknown_evidence_is_required(value):
    with pytest.raises(ValidationError):
        RequestItem.model_validate(value)


def test_resolution_and_revision_contracts_are_strict():
    for value in (
        {"revision": True},
        {"revision": "1"},
        {"revision": 0},
        {"revision": 1, "force": True},
    ):
        with pytest.raises(ValidationError):
            RequestRefresh.model_validate(value)
    with pytest.raises(ValidationError):
        RequestResolution(revision=1, action="satisfy", recording_id="rec", notes="")
    with pytest.raises(ValidationError):
        RequestResolution(revision=1, action="clear", source_url="https://soundcloud.com/a/b")
