"""Real tagged tones exercise organization, persistence, revision races and original bytes."""

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import mutagen
import pytest
from mutagen.id3 import COMM, TBPM, TCON, TKEY
from mutagen.wave import WAVE
from sqlalchemy import func, select

from djlib.application import organization as org
from djlib.application.service import Application
from djlib.audio.inspection import checksum
from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.domain.organization_contracts import AnnotationRequest, OrganizationRequest
from djlib.persistence.models import AssetRevision, Collection, Recording
from tests.conftest import execute


async def catalog(app, factory, specs):
    paths, tracks = [], []
    for index, spec in enumerate(specs):
        path = factory(f"organization-{index}.wav", frequency=210 + index * 50)
        audio = WAVE(path)
        audio.add_tags()
        for name, frame in (("bpm", TBPM), ("key", TKEY), ("genre", TCON)):
            if name in spec:
                audio.tags.add(frame(encoding=3, text=spec[name]))
        if "comment" in spec:
            audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=spec["comment"]))
        audio.save()
        paths.append(path)
        tracks.append(
            TrackInput(
                path=str(path),
                artist="Generated artist",
                title=spec.get("title", f"Tone {index}"),
                version=spec.get("version", ""),
            )
        )
    plan = app.plan(CollectionRequest(name="Source tones", tracks=tracks))
    job = app.start(StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key="source"))
    result = await execute(app, job["job_id"])
    assert result["outcome"] == "complete"
    entries = app.collection(result["result"]["collection_id"])["tracks"]
    refs = [{key: entry[key] for key in ("recording_id", "asset_revision_id")} for entry in entries]
    return paths, refs


def annotate(app, ref, revision=0, idempotency_key="annotate", **fields):
    return org.annotate_recording(
        app, AnnotationRequest(**ref, revision=revision, idempotency_key=idempotency_key, **fields)
    )


def organize(app, refs, key="organize", **fields):
    return org.organize_collection(
        app,
        OrganizationRequest(name="Generated warm-up", tracks=refs, idempotency_key=key, **fields),
    )


async def test_tag_provenance_unknowns_and_durable_annotations_preserve_sources(
    application, audio_factory
):
    paths, refs = await catalog(
        application,
        audio_factory,
        [
            {"bpm": "112.5", "key": "8A", "genre": "Downtempo", "comment": "Intro pad"},
            {},
            {"bpm": "fast", "key": "maybe C"},
            {"bpm": ["120", "125"]},
        ],
    )
    before = {p: checksum(p) for p in paths}
    first = org.inspect_metadata(application, **refs[0])
    assert first["embedded"]["bpm"] == {
        "value": 112.5,
        "known": True,
        "raw_values": ["112.5"],
        "source": "embedded_tag",
        "verified": False,
        "accuracy": "unverified",
    }
    assert first["effective"]["genres"] == ["Downtempo"]
    assert first["embedded"]["comments"]["values"] == ["Intro pad"]
    for ref in refs[1:]:
        unknown = org.inspect_metadata(application, **ref)
        assert unknown["effective"]["bpm"]["value"] is None
        assert not unknown["effective"]["bpm"]["known"]
        assert not unknown["acoustic_analysis_performed"]
    original = annotate(
        application,
        refs[0],
        notes="Long intro; start low",
        genres=["Warm house"],
        tags=["familiar", "opening"],
        set_role="arrival",
        energy=3,
        bpm={"value": 112.5, "source": "native_tag"},
        key={"value": "8A", "source": "operator", "verified": True},
    )
    assert original["state"] == "completed"
    assert not original["result"]["annotations"]["bpm"]["verified"]
    reopened = Application(application.workspace, application.db)
    persisted = org.inspect_metadata(reopened, **refs[0])
    assert persisted["effective"]["notes"] == "Long intro; start low"
    assert persisted["effective"]["genres"] == ["Warm house"]
    assert persisted["effective"]["key"]["accuracy"] == "operator_verified"
    assert {p: checksum(p) for p in paths} == before


async def test_annotation_patch_replay_and_stale_updates(application, audio_factory):
    _, refs = await catalog(application, audio_factory, [{}])
    first = annotate(application, refs[0], notes="Keep", tags=["opening"])
    assert (
        annotate(application, refs[0], notes="Keep", tags=["opening"])["job_id"] == first["job_id"]
    )
    with pytest.raises(AppError, match="different request") as conflict:
        annotate(application, refs[0], notes="Conflict")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    second = annotate(application, refs[0], revision=1, idempotency_key="patch", energy=4)
    assert second["result"]["annotations"]["notes"] == "Keep"
    with pytest.raises(AppError) as stale:
        annotate(application, refs[0], revision=1, idempotency_key="stale", notes="Lost update")
    assert stale.value.code == "ANNOTATION_STALE"
    cleared = annotate(application, refs[0], revision=2, idempotency_key="clear", notes=None)
    assert cleared["result"]["annotations"]["notes"] is None
    assert cleared["result"]["annotations"]["tags"] == ["opening"]
    assert cleared["result"]["revision"] == 3


async def test_native_tag_requires_matching_bytes_and_known_value(application, audio_factory):
    paths, refs = await catalog(application, audio_factory, [{"bpm": "120"}, {}])
    with pytest.raises(AppError) as mismatch:
        annotate(application, refs[0], bpm={"value": 121, "source": "native_tag"})
    assert mismatch.value.code == "NATIVE_TAG_MISMATCH"
    with pytest.raises(AppError) as missing:
        annotate(application, refs[1], bpm={"value": 120, "source": "native_tag"})
    assert missing.value.code == "NATIVE_TAG_MISMATCH"
    audio = WAVE(paths[0])
    audio.tags.add(TBPM(encoding=3, text="121"))
    audio.save()
    with pytest.raises(AppError) as changed:
        org.inspect_metadata(application, **refs[0])
    assert changed.value.code == "RECONCILIATION_REQUIRED"
    assert org.annotation_status(application, **refs[0])["revision"] == 0


async def test_ordered_filtered_collections_keep_exact_versions_and_overlap(
    application, audio_factory
):
    paths, refs = await catalog(
        application,
        audio_factory,
        [
            {
                "title": "Shared title",
                "version": "Radio Edit",
                "bpm": "110",
                "key": "2A",
                "genre": "House",
            },
            {
                "title": "Shared title",
                "version": "Extended",
                "bpm": "108",
                "key": "10A",
                "genre": "House",
            },
            {"bpm": "135", "key": "2A", "genre": "Techno"},
            {},
        ],
    )
    hashes = [checksum(path) for path in paths]
    for index in range(2):
        annotate(
            application,
            refs[index],
            idempotency_key=f"annotate-{index}",
            tags=["familiar"],
            set_role="warm-up",
            energy=index + 2,
        )
    before_counts = []
    with application.db.transaction() as session:
        for model in (Recording, AssetRevision):
            before_counts.append(session.scalar(select(func.count()).select_from(model)))
    first = organize(
        application,
        refs + [refs[0]],
        filters={
            "bpm_min": 100,
            "bpm_max": 120,
            "keys": ["2A", "10A"],
            "genres": ["house"],
            "tags": ["Familiar"],
            "set_roles": ["warm-up"],
        },
        order_by="bpm",
    )
    result = first["result"]
    assert result["selected_count"] == 2 and result["excluded_count"] == 3
    assert result["exclusion_counts"]["duplicate_reference"] == 1
    assert result["exclusion_counts"]["bpm_unknown"] == 1
    tracks = application.collection(result["collection_id"])["tracks"]
    assert [t["asset_revision_id"] for t in tracks] == [
        refs[1]["asset_revision_id"],
        refs[0]["asset_revision_id"],
    ]
    assert [t["version"] for t in tracks] == ["Extended", "Radio Edit"]
    second = organize(application, refs, key="key-order", order_by="key")
    second_tracks = application.collection(second["result"]["collection_id"])["tracks"]
    keys = [
        org.inspect_metadata(application, t["recording_id"], t["asset_revision_id"])["effective"][
            "key"
        ]["value"]
        for t in second_tracks
    ]
    assert keys == ["2A", "2A", "10A", None]
    with application.db.transaction() as session:
        after_counts = [
            session.scalar(select(func.count()).select_from(m)) for m in (Recording, AssetRevision)
        ]
    assert before_counts == after_counts
    assert [checksum(path) for path in paths] == hashes


async def test_unknown_policy_and_missing_catalog_rows_are_reported(application, audio_factory):
    _, refs = await catalog(application, audio_factory, [{"bpm": "120"}, {}, {"bpm": "140"}])
    included = organize(
        application,
        refs,
        unknown="include",
        filters={"bpm_max": 125},
        order_by="bpm",
        descending=True,
    )
    assert included["result"]["selected_count"] == 2
    assert included["result"]["unknown_included_count"] == 1
    assert included["result"]["exclusion_counts"] == {"bpm_outside_range": 1}
    assert [
        t["recording_id"]
        for t in application.collection(included["result"]["collection_id"])["tracks"]
    ] == [r["recording_id"] for r in refs[:2]]
    with pytest.raises(AppError) as error:
        organize(application, refs, key="error", unknown="error", filters={"bpm_max": 125})
    assert error.value.code == "ORGANIZATION_UNKNOWN"
    missing = {"recording_id": "missing-recording", "asset_revision_id": "missing-bytes"}
    result = organize(application, [missing], key="missing")["result"]
    assert result["selected_count"] == 0 and result["excluded_count"] == 1
    assert result["excluded"][0]["reasons"] == ["NOT_FOUND"]
    unverified = organize(
        application, refs[:1], key="unverified", filters={"bpm_min": 100, "require_verified": True}
    )
    assert unverified["result"]["exclusion_counts"] == {"bpm_unverified": 1}


async def test_collection_replay_freezes_decisions_and_rejects_revision_conflict(
    application, audio_factory
):
    _, refs = await catalog(application, audio_factory, [{"bpm": "110"}, {"bpm": "120"}])
    request = OrganizationRequest(
        name="Frozen", tracks=refs, filters={"bpm_max": 115}, idempotency_key="frozen"
    )
    first = org.organize_collection(application, request)
    annotate(application, refs[1], bpm={"value": 110, "source": "operator", "verified": True})
    replay = org.organize_collection(application, request)
    assert replay["job_id"] == first["job_id"]
    assert replay["result"]["selected_count"] == 1
    with application.db.transaction() as session:
        before = session.scalar(select(func.count()).select_from(Collection))
    wrong_revision = {**refs[0], "asset_revision_id": refs[1]["asset_revision_id"]}
    with pytest.raises(AppError) as conflict:
        organize(application, [refs[0], wrong_revision], key="conflict")
    assert conflict.value.code == "ORGANIZATION_REVISION_CONFLICT"
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Collection)) == before


async def test_concurrent_annotation_compare_and_swap_has_one_winner(application, audio_factory):
    _, refs = await catalog(application, audio_factory, [{}])
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            return annotate(
                application, refs[0], idempotency_key=f"race-{index}", notes=f"Writer {index}"
            )
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert "ANNOTATION_STALE" in outcomes
    status = org.annotation_status(application, **refs[0])
    assert status["revision"] == 1
    winner = next(item for item in outcomes if isinstance(item, dict))
    assert status["annotations"] == winner["result"]["annotations"]


@pytest.mark.parametrize(
    "extension,codec",
    [("flac", "flac"), ("mp3", "libmp3lame"), ("m4a", "aac"), ("aiff", "pcm_s16be")],
)
async def test_embedded_formats_are_read_without_guessed_analysis(
    application, audio_factory, extension, codec
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Compressed format fixture needs FFmpeg and ffprobe")
    source = audio_factory("format-source.wav")
    target = source.with_suffix(f".{extension}")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(source), "-c:a", codec, str(target)],
        check=True,
        capture_output=True,
    )
    media = mutagen.File(target)
    if media.tags is None:
        media.add_tags()
    if extension in {"mp3", "aiff"}:
        media.tags.add(TBPM(encoding=3, text="118"))
        media.tags.add(TKEY(encoding=3, text="12m"))
        media.tags.add(TCON(encoding=3, text="Synthetic"))
        media.tags.add(COMM(encoding=3, lang="eng", desc="", text="Original generated tone"))
    elif extension == "m4a":
        media["tmpo"] = [118]
        media["----:com.apple.iTunes:initialkey"] = [b"12m"]
        media["©gen"] = ["Synthetic"]
        media["©cmt"] = ["Original generated tone"]
    else:
        media["bpm"] = ["118"]
        media["initialkey"] = ["12m"]
        media["genre"] = ["Synthetic"]
        media["comment"] = ["Original generated tone"]
    media.save()
    digest = checksum(target)
    plan = application.plan(
        CollectionRequest(
            name="Format validation",
            tracks=[TrackInput(path=str(target), artist="Synthetic", title="Format tone")],
        )
    )
    accepted = application.start(
        StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key="format")
    )
    completed = await execute(application, accepted["job_id"])
    track = application.collection(completed["result"]["collection_id"])["tracks"][0]
    inspected = org.inspect_metadata(application, track["recording_id"], track["asset_revision_id"])
    assert inspected["effective"]["bpm"]["value"] == 118
    assert inspected["effective"]["key"]["value"] == "12m"
    assert not inspected["effective"]["bpm"]["verified"]
    assert inspected["effective"]["genres"] == ["Synthetic"]
    assert inspected["embedded"]["comments"]["values"] == ["Original generated tone"]
    assert checksum(target) == digest


async def test_changed_annotation_invalidates_collection_decisions(
    application, audio_factory, monkeypatch
):
    _, refs = await catalog(application, audio_factory, [{"bpm": "110"}])
    real_inspect = org.inspect_metadata

    def change_after_inspection(*args, **kwargs):
        result = real_inspect(*args, **kwargs)
        annotate(application, refs[0], bpm={"value": 140, "source": "operator"})
        return result

    monkeypatch.setattr(org, "inspect_metadata", change_after_inspection)
    with application.db.transaction() as session:
        before = session.scalar(select(func.count()).select_from(Collection))
    with pytest.raises(AppError) as stale:
        organize(application, refs, filters={"bpm_max": 120})
    assert stale.value.code == "ANNOTATION_STALE"
    with application.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Collection)) == before


async def test_completed_organization_jobs_cannot_be_requeued_or_cancelled(
    application, audio_factory
):
    _, refs = await catalog(application, audio_factory, [{}])
    annotated = annotate(application, refs[0], notes="Durable note")
    organized = organize(application, refs)
    before_annotation = org.annotation_status(application, **refs[0])
    before_collection = application.collection(organized["result"]["collection_id"])
    for job in (annotated, organized):
        for action in ("retry", "cancel", "pause", "resume"):
            with pytest.raises(AppError) as terminal:
                application.control(job["job_id"], action)
            assert terminal.value.code == "JOB_TERMINAL"
            assert application.job(job["job_id"])["state"] == "completed"
    assert org.annotation_status(application, **refs[0]) == before_annotation
    assert application.collection(organized["result"]["collection_id"]) == before_collection
