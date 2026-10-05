"""App-only delivery trials use original audio and explicit simulated operator evidence."""

import json
import shutil
from pathlib import Path

import mutagen
import pytest
from mutagen.id3 import TBPM, TKEY
from pydantic import ValidationError

from djlib.application import delivery
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.contracts import DeliveryObservation, DeliveryRequest
from djlib.domain.errors import AppError
from djlib.persistence.models import Job
from tests.conftest import execute, submit_collection

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="App working-copy trials require real FFmpeg and ffprobe",
)


async def prepared(app, factory, workflow="rekordbox_import", audio_mode="preserve"):
    source = factory("app-original.wav", frames=88200, artist="Test Artist", title="app-original")
    original = mutagen.File(source)
    original.tags.add(TBPM(encoding=3, text="88"))
    original.tags.add(TKEY(encoding=3, text="Dm"))
    original.save()
    source_hash = checksum(source)
    job = submit_collection(app, [source])
    completed = await execute(app, job["job_id"])
    assert completed["outcome"] == "complete"
    request = DeliveryRequest(
        name="Original-tone app trial",
        collection_ids=[completed["result"]["collection_id"]],
        workflow=workflow,
        app_version="synthetic-native-version",
        audio_mode=audio_mode,
        pilot_size=1,
    )
    status = delivery.create_delivery(app, request)
    preparation = delivery.prepare_delivery(app, status["delivery_id"], 1, "prepare-app")
    complete = await execute(app, preparation["job_id"])
    assert complete["outcome"] == "complete"
    manifest = json.loads(Path(complete["result"]["manifest_path"]).read_text())
    return source, source_hash, delivery.delivery_status(app, status["delivery_id"]), manifest


def observe(app, status, manifest, stage="imported"):
    return delivery.observe_delivery(
        app,
        status["delivery_id"],
        DeliveryObservation(
            revision=status["revision"],
            stage=stage,
            app_version=status["request"]["app_version"],
            track_count=manifest["unique_track_count"],
            playlist_counts=manifest["playlist_counts"],
            checked_recording_ids=[t["recording_id"] for t in manifest["tracks"]],
            observer="Synthetic test operator",
            notes="Explicit simulation; no native app, USB, or player was operated.",
            method="native_app_ui",
            outcome="passed",
        ),
    )


def add_analysis_tags(path, bpm="124", key="Am"):
    media = mutagen.File(path)
    media.tags.add(TBPM(encoding=3, text=bpm))
    media.tags.add(TKEY(encoding=3, text=key))
    media.save()


def analysis_observation(status, manifest):
    return DeliveryObservation(
        revision=status["revision"],
        stage="analyzed",
        app_version=status["request"]["app_version"],
        track_count=manifest["unique_track_count"],
        playlist_counts=manifest["playlist_counts"],
        checked_recording_ids=[t["recording_id"] for t in manifest["tracks"]],
        observer="Synthetic test operator",
        notes="Original-tone fixture; no native application was operated.",
        method="native_app_ui",
        outcome="passed",
    )


def verification_job(app, *, state="running", generation=3):
    with app.db.transaction() as session:
        job = Job(
            id="job-verification-fixture",
            kind="delivery_check",
            state=state,
            generation=generation,
            request={},
        )
        session.add(job)
    return "job-verification-fixture", 3


async def test_precomputed_analysis_checks_finalize_without_rehashing(
    application, audio_factory, monkeypatch
):
    source, source_hash, status, manifest = await prepared(application, audio_factory)
    status = observe(application, status, manifest)
    add_analysis_tags(Path(manifest["tracks"][0]["path"]))
    checked = json.loads(json.dumps(delivery._reconcile_analysis(application, manifest)))
    guard = verification_job(application)

    def unexpected_hash(*args, **kwargs):
        pytest.fail("The atomic finalizer must use checked worker results and cheap signatures")

    def manifest_hash_only(path):
        if Path(path).name == "delivery-manifest.json":
            return checksum(path)
        return unexpected_hash(path)

    monkeypatch.setattr(delivery, "checksum", manifest_hash_only)
    monkeypatch.setattr(delivery, "inspect_audio", unexpected_hash)
    monkeypatch.setattr(delivery, "pcm_hash", unexpected_hash)
    status = delivery.observe_delivery(
        application,
        status["delivery_id"],
        analysis_observation(status, manifest),
        reconciled=checked,
        job_guard=guard,
    )
    receipt = application.job(guard[0])
    assert receipt["state"] == "completed"
    assert receipt["result"]["evidence_committed"] is True
    assert receipt["result"]["revision"] == status["revision"]
    assert not status["ready_for_app_use"]
    status = delivery.verify_app(
        application, status["delivery_id"], status["revision"], reconciled=checked
    )
    assert status["ready_for_app_use"]
    assert checksum(source) == source_hash


@pytest.mark.parametrize("change", ["tags", "audio"])
async def test_deferred_analysis_rejects_file_changes_and_atomically_fails_job(
    application, audio_factory, change
):
    _, _, status, manifest = await prepared(application, audio_factory)
    status = observe(application, status, manifest)
    checked = delivery._reconcile_analysis(application, manifest)
    working = Path(manifest["tracks"][0]["path"])
    if change == "tags":
        add_analysis_tags(working)
    else:
        shutil.copyfile(audio_factory("different.wav", frequency=660, frames=88200), working)
    guard = verification_job(application)
    with pytest.raises(AppError) as error:
        delivery.observe_delivery(
            application,
            status["delivery_id"],
            analysis_observation(status, manifest),
            reconciled=checked,
            job_guard=guard,
        )
    assert error.value.code == "RECONCILIATION_STALE"
    current = delivery.delivery_status(application, status["delivery_id"])
    assert current["evidence"]["analyzed"]["outcome"] == "failed"
    receipt = application.job(guard[0])
    assert receipt["state"] == "failed"
    assert receipt["result"]["revision"] == current["revision"]
    assert receipt["result"]["error"]["code"] == "RECONCILIATION_STALE"


@pytest.mark.parametrize("state,generation", [("paused", 3), ("cancelled", 3), ("running", 4)])
@pytest.mark.parametrize("operation", ["analyzed", "verify-app"])
async def test_deferred_check_cannot_commit_after_pause_cancel_or_generation_change(
    application, audio_factory, state, generation, operation
):
    _, _, status, manifest = await prepared(application, audio_factory)
    status = observe(application, status, manifest)
    checked = delivery._reconcile_analysis(application, manifest)
    if operation == "verify-app":
        status = observe(application, status, manifest, "analyzed")
    guard = verification_job(application, state=state, generation=generation)
    with pytest.raises(AppError) as error:
        if operation == "analyzed":
            delivery.observe_delivery(
                application,
                status["delivery_id"],
                analysis_observation(status, manifest),
                reconciled=checked,
                job_guard=guard,
            )
        else:
            delivery.verify_app(
                application,
                status["delivery_id"],
                status["revision"],
                reconciled=checked,
                job_guard=guard,
            )
    assert error.value.code == "JOB_STALE"
    current = delivery.delivery_status(application, status["delivery_id"])
    assert current["revision"] == status["revision"]
    assert current["evidence"] == status["evidence"]
    assert application.job(guard[0])["state"] == state


async def test_precomputed_app_check_cannot_accept_new_tags_as_old_analysis(
    application, audio_factory
):
    _, _, status, manifest = await prepared(application, audio_factory)
    for stage in ("imported", "analyzed"):
        status = observe(application, status, manifest, stage)
    add_analysis_tags(Path(manifest["tracks"][0]["path"]))
    checked = delivery._reconcile_analysis(application, manifest)
    with pytest.raises(AppError) as error:
        delivery.verify_app(
            application, status["delivery_id"], status["revision"], reconciled=checked
        )
    assert error.value.code == "ANALYSIS_CHANGED"
    assert not delivery.delivery_status(application, status["delivery_id"])[
        "app_requirements_met_at_last_check"
    ]


@pytest.mark.parametrize("workflow", ["rekordbox_import", "serato_import"])
async def test_app_import_analysis_and_fresh_verification_need_no_hardware_or_usb(
    application, audio_factory, monkeypatch, workflow
):
    def forbidden_device(*args, **kwargs):
        pytest.fail("An app-only workflow must not inspect or require a USB")

    monkeypatch.setattr(delivery, "inspect_device", forbidden_device)
    source, source_hash, status, manifest = await prepared(application, audio_factory, workflow)
    assert status["request"]["hardware_profile"] is None
    assert status["prepared_for_import"]
    assert "bind_target_usb" not in status["blockers"]
    assert not status["ready_for_app_use"]
    with pytest.raises(AppError) as error:
        delivery.verify_app(application, status["delivery_id"], status["revision"])
    assert error.value.code == "APP_STAGE_REQUIRED"
    status = observe(application, status, manifest)
    working = Path(manifest["tracks"][0]["path"])
    add_analysis_tags(working)
    status = observe(application, status, manifest, "analyzed")
    assert not status["ready_for_app_use"]
    assert status["evidence"]["analyzed"]["evidence_level"] == "operator_reported"
    checked = delivery.verify_app(application, status["delivery_id"], status["revision"])
    assert checked["ready_for_app_use"]
    assert checked["app_requirements_met_at_last_check"]
    assert checked["ready_for_departure"] is False
    assert checked["hardware_verified_automatically"] is False
    assert checked["native_automation_available"] is False
    assert checked["current_device"] is None
    assert checked["blockers"] == []
    assert checksum(source) == source_hash
    stored = delivery.delivery_status(application, status["delivery_id"])
    assert stored["ready_for_app_use"] is False
    assert stored["app_requirements_met_at_last_check"] is True
    assert stored["hashes_rechecked_by_status"] is False
    assert stored["next_step"] == "verify_app_before_use"
    for action in (
        lambda: delivery.bind_device(
            application, stored["delivery_id"], stored["revision"], "/not-a-device"
        ),
        lambda: delivery.verify_device(application, stored["delivery_id"], stored["revision"]),
        lambda: observe(application, stored, manifest, "native_exported"),
    ):
        with pytest.raises(AppError) as error:
            action()
        assert error.value.code == "WORKFLOW_SCOPE"


@pytest.mark.parametrize("workflow", ["rekordbox_import", "serato_import"])
async def test_app_full_preparation_requires_completed_matching_pilot(
    application, audio_factory, workflow
):
    _, _, pilot, manifest = await prepared(application, audio_factory, workflow)
    request = DeliveryRequest.model_validate(
        {**pilot["request"], "phase": "full", "pilot_delivery_id": pilot["delivery_id"]}
    )
    full = delivery.create_delivery(application, request)
    with pytest.raises(AppError) as error:
        delivery.prepare_delivery(application, full["delivery_id"], 1, "before-trial")
    assert error.value.code == "PILOT_REQUIRED"
    for stage in ("imported", "analyzed"):
        pilot = observe(application, pilot, manifest, stage)
    with pytest.raises(AppError) as error:
        delivery.prepare_delivery(application, full["delivery_id"], 1, "before-readback")
    assert error.value.code == "PILOT_REQUIRED"
    pilot = delivery.verify_app(application, pilot["delivery_id"], pilot["revision"])
    assert pilot["ready_for_app_use"]
    job = delivery.prepare_delivery(application, full["delivery_id"], 1, "after-trial")
    assert (await execute(application, job["job_id"]))["outcome"] == "complete"
    for field, value in (
        ("audio_mode", "mp3_320"),
        ("app_version", "another-version"),
        ("workflow", "serato_import" if workflow == "rekordbox_import" else "rekordbox_import"),
    ):
        mismatched = delivery.create_delivery(
            application, request.model_copy(update={field: value})
        )
        with pytest.raises(AppError) as error:
            delivery.prepare_delivery(
                application, mismatched["delivery_id"], 1, f"mismatched-{field}"
            )
        assert error.value.code == "PILOT_REQUIRED"


@pytest.mark.parametrize("change", ["audio", "missing", "tags"])
async def test_fresh_app_check_invalidates_analysis_after_working_bytes_change(
    application, audio_factory, change
):
    source, source_hash, status, manifest = await prepared(application, audio_factory)
    for stage in ("imported", "analyzed"):
        status = observe(application, status, manifest, stage)
    status = delivery.verify_app(application, status["delivery_id"], status["revision"])
    assert status["ready_for_app_use"]
    working = Path(manifest["tracks"][0]["path"])
    if change == "missing":
        working.unlink()
    elif change == "tags":
        add_analysis_tags(working, bpm="137")
    else:
        replacement = audio_factory("replacement-original.wav", frequency=770, frames=88200)
        shutil.copyfile(replacement, working)
    with pytest.raises(AppError) as error:
        delivery.verify_app(application, status["delivery_id"], status["revision"])
    if change == "tags":
        assert error.value.code == "ANALYSIS_CHANGED"
    current = delivery.delivery_status(application, status["delivery_id"])
    assert current["evidence"]["analyzed"]["outcome"] == "failed"
    assert current["evidence"]["app_readback"]["status"] == "failed"
    assert current["revision"] > status["revision"]
    assert not current["ready_for_app_use"]
    assert not current["app_requirements_met_at_last_check"]
    assert checksum(source) == source_hash


@pytest.mark.parametrize("mode", ["mp3_320", "wav16_44100"])
async def test_converted_app_copies_accept_analysis_tags_without_changing_originals(
    application, audio_factory, mode
):
    source, source_hash, status, manifest = await prepared(
        application, audio_factory, "serato_import", mode
    )
    track = manifest["tracks"][0]
    working = Path(track["path"])
    assert track["conversion"] == mode
    measured = inspect_audio(working)
    assert measured.sample_rate == 44100
    assert measured.channels == 2
    assert measured.artist == "Test Artist"
    assert measured.title == "app-original"
    assert working != source
    assert track["source_sha256"] == source_hash
    status = observe(application, status, manifest)
    add_analysis_tags(working)
    status = observe(application, status, manifest, "analyzed")
    assert status["evidence"]["analyzed"]["assets"][0]["sha256"] == checksum(working)
    checked = delivery.verify_app(application, status["delivery_id"], status["revision"])
    assert checked["ready_for_app_use"]
    assert checksum(source) == source_hash
    original_tags = mutagen.File(source).tags
    assert str(original_tags["TBPM"]) == "88"
    assert str(original_tags["TKEY"]) == "Dm"


@pytest.mark.parametrize("workflow", ["rekordbox_import", "serato_import"])
def test_app_request_rejects_a_hardware_target_instead_of_silently_using_it(workflow):
    with pytest.raises(ValidationError):
        DeliveryRequest(
            name="App-only",
            collection_ids=["synthetic-collection"],
            workflow=workflow,
            app_version="test",
            hardware_profile="cdj-3000",
        )
