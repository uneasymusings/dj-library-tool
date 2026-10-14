"""Original-tone request ledgers preserve uncertainty, exact versions, and CAS evidence."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from djlib.application import requests
from djlib.application.set_identification import build_tracklist, request_items
from djlib.domain.contracts import (
    CollectionRequest,
    StartRequest,
    TrackInput,
    artist_names,
    credit_form,
    split_featured,
)
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import (
    RequestCreate,
    RequestItem,
    RequestRefresh,
    RequestResolution,
)
from djlib.interfaces.rekordbox_cli import set_item
from djlib.persistence.models import Job
from djlib.persistence.request_models import RequestLedger
from tests.conftest import execute
from tests.test_set_identification import SET_URL, listing, song


@pytest.fixture
def ledger_app(application):
    # Use the packaged migrations, including the real persistent request tables.
    return application


async def owned(
    app,
    factory,
    *,
    name="owned",
    artist="Original Artist",
    title="Original Tone",
    version="",
    frequency=440,
):
    source = factory(f"{name}.wav", frequency=frequency)
    plan = app.plan(
        CollectionRequest(
            name=name,
            tracks=[TrackInput(path=str(source), artist=artist, title=title, version=version)],
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
    # The same list again returns the stored report, marked as reused so callers can re-check it.
    assert same.pop("reused") is True and same == result
    assert checked == [track["path"]]
    with ledger_app.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(RequestLedger)) == 1
        assert (
            session.scalar(select(func.count()).select_from(Job)) == 1
        )  # original catalog job only


async def test_file_replaced_at_open_is_not_reused_even_with_identical_bytes(
    ledger_app, audio_factory, monkeypatch
):
    track = await owned(ledger_app, audio_factory)
    path = Path(track["path"])
    replacement = path.with_name("replacement.wav")
    replacement.write_bytes(path.read_bytes())
    before = path.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    real_open = os.open

    def swap_before_open(value, flags, *args, **kwargs):
        assert Path(value) == path
        os.replace(replacement, path)
        return real_open(value, flags, *args, **kwargs)

    monkeypatch.setattr(requests.os, "open", swap_before_open)
    ledger = create(ledger_app, [named()])
    item = ledger["items"][0]
    assert item["state"] == "unavailable"
    assert item["accepted"] is None
    assert item["candidates"][0]["availability"] == "changed"


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


@pytest.mark.parametrize(
    ("artist", "names"),
    [
        ("Jamie Jones, Max Dean, Luke Dean", ("jamie jones", "luke dean", "max dean")),
        ("Max Dean, Luke Dean & Jamie Jones", ("jamie jones", "luke dean", "max dean")),
        ("A; B / C", ("a", "b", "c")),
        ("A x B", ("a", "b")),
        ("Busta Rhymes X Janet Jackson", ("busta rhymes", "janet jackson")),
        ("Malcolm X", ("malcolm x",)),  # an X that ends a name is part of it
        ("A feat. B", ("a", "b")),
        ("A Ft B", ("a", "b")),
        ("A featuring B", ("a", "b")),
        ("A vs. B", ("a", "b")),
        ("A (feat. B)", ("a", "b")),
        ("A, A", ("a",)),
        ("/", ("/",)),  # symbol-only credits never collapse to an empty set
    ],
)
def test_artist_credits_split_into_a_sorted_set_of_names(artist, names):
    assert artist_names(artist) == names


@pytest.mark.parametrize(
    ("title", "split"),
    [
        ("Rain (feat. Ana)", ("Rain", ("Ana",))),
        ("Rain [ft. Ana]", ("Rain", ("Ana",))),
        ("Rain feat. Ana", ("Rain", ("Ana",))),
        ("Rain feat. Ana (Extended Mix)", ("Rain (Extended Mix)", ("Ana",))),
        ("Rain (feat. Ana & Bo) (Dub)", ("Rain (Dub)", ("Ana & Bo",))),
        ("Rain (Ana feat. Bo Remix)", ("Rain (Ana feat. Bo Remix)", ())),
        ("No Mean Feat", ("No Mean Feat", ())),
    ],
)
def test_featured_artists_written_in_the_title_move_to_the_credits(title, split):
    assert split_featured(title) == split
    assert credit_form("Cy", title) == credit_form(", ".join(("Cy", *split[1])), split[0])


async def test_tracklist_artist_order_does_not_hide_an_owned_mix(ledger_app, audio_factory):
    # The real case: tags credit "Jamie Jones, Max Dean, Luke Dean"; the tracklist lists the
    # same three artists in another order.
    source = audio_factory(
        "gets.wav",
        frequency=523,
        artist="Jamie Jones, Max Dean, Luke Dean",
        title="Gets Like That (Jamie Jones Remix)",
    )
    job = ledger_app.scan(str(source.parent), "scan")
    assert (await execute(ledger_app, job["job_id"]))["outcome"] == "complete"
    track = next(t for t in ledger_app.library(limit=100)["tracks"] if t["path"] == str(source))
    tracklist = "Max Dean, Luke Dean, Jamie Jones"
    result = create(
        ledger_app,
        [
            named(artist=tracklist, title="Gets Like That (Jamie Jones Remix)"),
            named(artist=tracklist, title="Gets Like That (Original Mix)"),
        ],
    )
    remix, original = result["items"]
    assert remix["state"] == "satisfied"
    assert remix["accepted"]["recording_id"] == track["recording_id"]
    assert remix["accepted"]["identity_match"] == "equivalent_labels"
    assert remix["accepted"]["availability"] == "verified"
    # Another mix is never owned, but it is reported: "you own: Jamie Jones Remix".
    assert original["state"] == "missing"
    assert original["accepted"] is None
    assert [(c["recording_id"], c["identity_match"]) for c in original["candidates"]] == [
        (track["recording_id"], "different_version")
    ]
    assert original["candidates"][0]["availability"] == "not_checked"
    assert set_item(original)["you_own"] == ["Jamie Jones Remix"]


@pytest.mark.parametrize(
    ("catalog", "requested", "match"),
    [
        (("B, A", "Original Tone"), ("A, B", "Original Tone"), "equivalent_labels"),
        # Punctuation-only separators already normalize alike ("a b").
        (("A & B", "Original Tone"), ("A, B", "Original Tone"), "exact_labels"),
        (("B & A", "Original Tone"), ("A, B", "Original Tone"), "equivalent_labels"),
        (("A feat. B", "Original Tone"), ("A", "Original Tone (feat. B)"), "equivalent_labels"),
        (("A", "Original Tone (feat. B)"), ("A feat. B", "Original Tone"), "equivalent_labels"),
        (
            ("B x A", "Original Tone [ft. C] (Dub)"),
            ("C; A & B", "Original Tone (Dub)"),
            "equivalent_labels",
        ),
    ],
)
async def test_same_credits_in_any_order_or_separator_are_owned(
    ledger_app, audio_factory, catalog, requested, match
):
    track = await owned(ledger_app, audio_factory, artist=catalog[0], title=catalog[1])
    item = create(ledger_app, [named(artist=requested[0], title=requested[1])])["items"][0]
    assert item["state"] == "satisfied"
    assert item["accepted"]["recording_id"] == track["recording_id"]
    assert item["accepted"]["identity_match"] == match


@pytest.mark.parametrize(
    ("catalog", "requested", "hints"),
    [
        (("A, B", "Original Tone"), ("A", "Original Tone"), []),
        (("A feat. B", "Original Tone"), ("A", "Original Tone"), []),
        (("A", "Original Tone"), ("A & B", "Original Tone"), []),
        # Already shown as another version before credits were compared; still not owned.
        (("A", "Original Tone (feat. B)"), ("A", "Original Tone"), ["different_version"]),
    ],
)
async def test_a_subset_of_the_credited_artists_is_not_owned(
    ledger_app, audio_factory, catalog, requested, hints
):
    await owned(ledger_app, audio_factory, artist=catalog[0], title=catalog[1])
    item = create(ledger_app, [named(artist=requested[0], title=requested[1])])["items"][0]
    assert item["state"] == "missing"
    assert item["accepted"] is None
    assert [c["identity_match"] for c in item["candidates"]] == hints


async def test_reordered_credits_on_two_recordings_need_an_explicit_choice(
    ledger_app, audio_factory
):
    await owned(ledger_app, audio_factory, name="first", artist="A, B", frequency=220)
    second = await owned(ledger_app, audio_factory, name="second", artist="B & A", frequency=330)
    result = create(ledger_app, [named(artist="A, B")])
    item = result["items"][0]
    assert item["state"] == "ambiguous"
    assert sorted(c["identity_match"] for c in item["candidates"]) == [
        "equivalent_labels",
        "exact_labels",
    ]
    selected = resolve(
        ledger_app,
        result,
        action="satisfy",
        recording_id=second["recording_id"],
        asset_revision_id=second["asset_revision_id"],
        notes="Same artists credited in another order",
    )
    assert selected["items"][0]["state"] == "satisfied"
    assert selected["items"][0]["accepted"]["recording_id"] == second["recording_id"]
    assert selected["items"][0]["accepted"]["basis"] == "operator_selection"


async def test_title_lookup_matches_count_toward_the_candidate_bound(
    ledger_app, audio_factory, monkeypatch
):
    await owned(ledger_app, audio_factory, name="first", artist="A, B", frequency=220)
    await owned(ledger_app, audio_factory, name="second", artist="B, A", frequency=330)
    monkeypatch.setattr(requests, "MAX_CANDIDATES", 1)
    item = create(ledger_app, [named(artist="A, B")])["items"][0]
    # The reordered match did not fit; it must make the item ambiguous, not hide silently.
    assert item["state"] == "ambiguous"
    assert item["candidates_truncated"] is True
    assert [c["identity_match"] for c in item["candidates"]] == ["exact_labels"]


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
    report = json.loads(path.read_text(encoding="utf-8"))
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
    assert json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))["items"] == []


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
    # Symbol identities now have distinct catalog keys; unrelated symbols are
    # excluded by the index itself instead of becoming mismatched candidates.
    assert result["items"][0]["candidates"] == []
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
        {"kind": "unknown"},
        {"kind": "unknown", "label": "ID", "timestamp": "1:02", "artist": "Guess"},
        {"kind": "unknown", "label": "ID", "source_url": "file:///private/music"},
        # An ID has no spelling to own it under; a named song has at most one more.
        {"kind": "unknown", "label": "ID", "also": [{"artist": "Guess", "title": "Song"}]},
        {"artist": "A", "title": "T", "also": [{"artist": "B", "title": "T"}] * 2},
        {"artist": "A", "title": "T", "also": [{"artist": "B"}]},
        {"artist": "A", "title": "T", "also": [{"artist": "B", "title": "T", "source": "x" * 41}]},
    ],
)
def test_request_items_are_strict_and_unknown_evidence_is_required(value):
    with pytest.raises(ValidationError):
        RequestItem.model_validate(value)


def test_an_id_keeps_its_place_without_a_time():
    """ "5. ID - ID" in a pasted list is an ID at number 5, even with no time or link."""
    item = RequestItem.model_validate({"kind": "unknown", "label": "ID"})
    assert (item.label, item.timestamp, item.source_url) == ("ID", None, None)


def test_other_spellings_leave_saved_lists_and_their_keys_as_they_were():
    """Items without ``also`` serialize as before it existed, so lists saved by 0.1.0a15 and
    the keys made from them still match; one with ``also`` carries it."""
    plain = named().model_dump(mode="json")
    assert "also" not in plain and "also" not in named().model_dump()
    credit = {"artist": "B", "title": "T", "version": "", "source": "mixesdb"}
    assert named(also=[credit]).model_dump(mode="json")["also"] == [credit]
    body = RequestCreate(name="n", items=[named()], idempotency_key="k")
    assert body.model_dump(mode="json")["items"] == [plain]


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


async def test_original_mix_means_no_special_version(ledger_app, audio_factory):
    await owned(ledger_app, audio_factory, name="bare", artist="Jeff Mills", title="The Bells")
    await owned(
        ledger_app,
        audio_factory,
        name="tagged",
        artist="Kerri Chandler",
        title="Rain (Original Mix)",
        frequency=330,
    )
    await owned(
        ledger_app,
        audio_factory,
        name="radio",
        artist="Lumen",
        title="Halo",
        version="Radio Edit",
        frequency=550,
    )
    result = create(
        ledger_app,
        [
            named(artist="Jeff Mills", title="The Bells (Original Mix)"),
            named(artist="Jeff Mills", title="The Bells", version="Original Mix"),
            named(artist="Kerri Chandler", title="Rain"),
            named(artist="Lumen", title="Halo (Original Mix)"),
        ],
    )
    states = [item["state"] for item in result["items"]]
    assert states == ["satisfied", "satisfied", "satisfied", "missing"]
    lumen = result["items"][3]["candidates"]
    assert [c["identity_match"] for c in lumen] == ["different_version"]


GLASPER = {
    "artist": "Robert Glasper Experiment",
    "title": "Move Love",
    "version": "Kaytranada Edition",
}


def listed_as(**changes):
    """MixesDB's spelling of the song Shazam heard as GLASPER."""
    return {
        "artist": "Robert Glasper",
        "title": "Move Love",
        "version": "Kaytranada Edition",
        "source": "mixesdb",
        **changes,
    }


async def test_a_song_is_owned_under_the_listings_spelling_too(ledger_app, audio_factory):
    """Shazam credits "Robert Glasper Experiment"; the DJ's file, like MixesDB, says "Robert
    Glasper". Each spelling is matched exactly; the crate takes the file."""
    track = await owned(
        ledger_app, audio_factory, artist="Robert Glasper", title="Move Love (Kaytranada Edition)"
    )
    result = create(ledger_app, [named(**GLASPER, also=[listed_as()]), named(**GLASPER)])
    item, plain = result["items"]
    assert item["state"] == "satisfied"
    accepted = item["accepted"]
    assert accepted["recording_id"] == track["recording_id"]
    assert accepted["basis"] == "exact_catalog_labels"
    assert accepted["identity_match"] == "equivalent_labels"
    assert accepted["matched_as"] == "Robert Glasper - Move Love (Kaytranada Edition)"
    assert accepted["matched_as_source"] == "mixesdb"
    # Without the listing's spelling, the same song stays missing: names are never loosened.
    assert plain["state"] == "missing" and plain["accepted"] is None
    collection = requests.collect_request(ledger_app, result["request_id"], result["revision"])
    assert collection["state"] == "queued"


async def test_another_spelling_never_loosens_the_mix(ledger_app, audio_factory):
    await owned(
        ledger_app, audio_factory, artist="Robert Glasper", title="Move Love (Kaytranada Edition)"
    )
    item = create(ledger_app, [named(**GLASPER, also=[listed_as(version="Kaytranada Remix")])])[
        "items"
    ][0]
    assert item["state"] == "missing" and item["accepted"] is None
    # The file is another mix of that spelling: shown as one you own, never in the crate.
    assert [c["identity_match"] for c in item["candidates"]] == ["different_version"]
    assert set_item(item)["you_own"] == ["Kaytranada Edition"]


async def test_a_mix_listed_but_not_heard_stays_another_mix_you_own(ledger_app, audio_factory):
    """Shazam heard the Kaytranada Remix where MixesDB lists the Edition, which the DJ owns:
    the set's song stays missing, with the Edition as a mix you own."""
    await owned(
        ledger_app, audio_factory, artist="Robert Glasper", title="Move Love (Kaytranada Edition)"
    )
    heard = song(0, 3, "Robert Glasper Experiment", "Move Love (Kaytranada Remix)")
    listed = listing("mixesdb", "1. [00:00] Robert Glasper - Move Love (Kaytranada Edition)")
    tracklist = build_tracklist(duration=300, probes=heard, listed=[listed])
    items = [RequestItem(**item) for item in request_items(tracklist, SET_URL)]
    item = create(ledger_app, items)["items"][0]
    assert item["state"] == "missing" and item["accepted"] is None
    assert set_item(item)["you_own"] == ["Kaytranada Edition"]


async def test_two_files_under_the_other_spelling_need_a_choice(ledger_app, audio_factory):
    labels = {"artist": "Robert Glasper", "title": "Move Love (Kaytranada Edition)"}
    await owned(ledger_app, audio_factory, name="first", frequency=220, **labels)
    second = await owned(ledger_app, audio_factory, name="second", frequency=330, **labels)
    result = create(ledger_app, [named(**GLASPER, also=[listed_as()])])
    assert result["items"][0]["state"] == "ambiguous"
    # Either file may be chosen: it matches the item's other spelling.
    chosen = resolve(
        ledger_app,
        result,
        action="satisfy",
        recording_id=second["recording_id"],
        asset_revision_id=second["asset_revision_id"],
        notes="The DJ picked the second copy",
    )
    accepted = chosen["items"][0]["accepted"]
    assert accepted["asset_revision_id"] == second["asset_revision_id"]
    assert accepted["basis"] == "operator_selection"
    assert accepted["matched_as"] == "Robert Glasper - Move Love (Kaytranada Edition)"


@pytest.mark.parametrize(
    ("catalog", "requested"),
    [
        (("De La Soul", "Me, Myself & I"), ("De La Soul", "Me Myself and I")),
        (("De La Soul", "Me Myself and I"), ("De La Soul", "Me, Myself & I")),
        (
            ("Fahlberg", "Bad Love (Antdot & Maz (BR) Remix)"),
            ("Fahlberg", "Bad Love (Antdot & Maz Remix)"),
        ),
        (
            ("Fahlberg", "Bad Love (Antdot & Maz Remix)"),
            ("Fahlberg", "Bad Love (Antdot & Maz (BR) Remix)"),
        ),
    ],
)
async def test_and_for_ampersand_and_a_remixers_country_tag_still_match(
    ledger_app, audio_factory, catalog, requested
):
    track = await owned(ledger_app, audio_factory, artist=catalog[0], title=catalog[1])
    item = create(ledger_app, [named(artist=requested[0], title=requested[1])])["items"][0]
    assert item["state"] == "satisfied"
    assert item["accepted"]["recording_id"] == track["recording_id"]
    assert item["accepted"]["identity_match"] == "equivalent_labels"


@pytest.mark.parametrize(
    ("catalog", "requested", "you_own"),
    [
        (("Lumen", "Rain"), ("Lumen", "Rain (VIP)"), ["Rain"]),
        (("Lumen", "Salt and Pepper (Extended Mix)"), ("Lumen", "Salt & Pepper (Dub)"), None),
    ],
)
async def test_folded_titles_are_still_exact_about_the_mix(
    ledger_app, audio_factory, catalog, requested, you_own
):
    await owned(ledger_app, audio_factory, artist=catalog[0], title=catalog[1])
    item = create(ledger_app, [named(artist=requested[0], title=requested[1])])["items"][0]
    assert item["state"] == "missing" and item["accepted"] is None
    assert [c["identity_match"] for c in item["candidates"]] == ["different_version"]
    assert set_item(item)["you_own"] == (you_own or ["Extended Mix"])


def test_matching_folds_leave_identity_keys_as_they_were():
    from djlib.domain.contracts import recording_key

    assert [
        recording_key(*labels)
        for labels in (
            ("De La Soul", "Me, Myself & I"),
            ("De La Soul", "Me Myself and I"),
            ("Fahlberg", "Bad Love (Antdot & Maz (BR) Remix)"),
            ("Antdot & Maz (BR) & Flip", "Deixa Passar"),
            ("Lumen", "Rain", "VIP"),
        )
    ] == [
        "de la soul|me myself i|",
        "de la soul|me myself and i|",
        "fahlberg|bad love antdot maz br remix|",
        "antdot maz br flip|deixa passar|",
        "lumen|rain|vip",
    ]


def same_list(app, items, key):
    return requests.create_request(
        app, RequestCreate(name="Friday Set", items=items, idempotency_key=key), same_list=True
    )


async def test_the_same_set_checked_again_updates_its_list_in_place(ledger_app, audio_factory):
    """A set run again, by a newer djlib with other notes or after more listening, keeps its
    list (and so its crate and rekordbox playlist) instead of making "Friday Set (2)"."""
    await owned(
        ledger_app, audio_factory, artist="Robert Glasper", title="Move Love (Kaytranada Edition)"
    )
    first = same_list(
        ledger_app,
        [named(**GLASPER, notes="Shazam: 3 windows"), RequestItem(kind="unknown", label="ID")],
        "identified:a15",
    )
    assert first["items"][0]["state"] == "missing"
    resolve(
        ledger_app,
        {**first, "items": first["items"][1:]},
        action="select_source",
        source_url="https://soundcloud.com/a/id",
        notes="Operator pick",
    )
    again = [
        named(**GLASPER, notes="Shazam heard it at 00:00 (3×)", also=[listed_as()]),
        RequestItem(kind="unknown", label="ID - ID", timestamp="03:00"),
    ]
    revised = same_list(ledger_app, again, "identified:a16")
    assert revised["request_id"] == first["request_id"] and revised.pop("reused") is True
    assert revised["revision"] == 3
    assert [item["item_id"] for item in revised["items"]] == [
        item["item_id"] for item in first["items"]
    ]
    assert revised["items"][0]["input"] == again[0].model_dump(mode="json")
    assert revised["items"][1]["input"]["timestamp"] == "03:00"
    assert revised["items"][1]["source_selection"]["notes"] == "Operator pick"
    # The re-check that callers run on a reused list finds the song under its other spelling.
    checked = requests.refresh_request(
        ledger_app, revised["request_id"], RequestRefresh(revision=3)
    )
    assert checked["items"][0]["state"] == "satisfied" and checked["revision"] == 4
    # The new key names the list now; the same run again changes nothing.
    assert same_list(ledger_app, again, "identified:a16")["revision"] == 4
    with ledger_app.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(RequestLedger)) == 1


def test_only_the_same_songs_under_the_same_name_reuse_a_list(ledger_app):
    first = same_list(ledger_app, [named(), named(title="Second")], "one")
    for number, (name, items) in enumerate(
        (
            ("Friday Set", [named(title="Second"), named()]),  # another order
            ("Friday Set", [named(), named(title="Second", version="Dub")]),  # another mix
            ("Friday Set", [named(), named(title="Second"), named(title="Third")]),
            ("Saturday Set", [named(), named(title="Second")]),
        )
    ):
        made = requests.create_request(
            ledger_app,
            RequestCreate(name=name, items=items, idempotency_key=f"other-{number}"),
            same_list=True,
        )
        assert made["request_id"] != first["request_id"] and "reused" not in made
    # Without same_list (assistants, `djlib requests create --file`), a new key is a new list.
    plain = create(ledger_app, [named(), named(title="Second")], key="assistant")
    assert plain["request_id"] != first["request_id"]


def test_tracklist_lines_with_control_characters_or_hidden_marks_still_read():
    """Mojibake ("\\x80\\x99" for "’") or an escape code in a pasted or published line loses no
    track and never fails the set; marks that reorder text can't disguise a name."""
    from djlib.application.tracklists import parse_tracklist

    text = (
        "01:40 ID - ID (Bicep\x80\x99s edit)\n"
        "02:00 Lumen - \u202eolaH\u202c\n"
        "03:00 Ana\x1b[2J - Rain"
    )
    items, skipped = parse_tracklist(text)
    assert skipped == []
    assert [(i.kind, i.label or i.title) for i in items] == [
        ("unknown", "ID - ID (Biceps edit)"),
        ("named", "olaH"),
        ("named", "Rain"),
    ]
    assert items[2].artist == "Ana[2J"
