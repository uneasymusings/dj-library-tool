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
