"""Generated-audio delivery trials with explicit, simulated native observations."""

import json
import shutil
from pathlib import Path

import pytest
from mutagen.id3 import TBPM, TKEY
from mutagen.wave import WAVE
from pydantic import ValidationError
from sqlalchemy import func, select

from djlib.application import delivery
from djlib.audio.inspection import checksum
from djlib.domain.contracts import (
    CollectionRequest,
    DeliveryObservation,
    DeliveryRequest,
    StartRequest,
    TrackInput,
)
from djlib.domain.errors import AppError
from djlib.exporting import device_readback
from djlib.exporting.delivery_media import pcm_hash
from djlib.jobs.worker import Worker
from djlib.persistence.models import AssetRevision, Recording
from tests.conftest import execute, submit_collection


@pytest.fixture
def media_dependencies():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Delivery working copies require FFmpeg and ffprobe")


async def catalog(application, audio_factory):
    paths = [audio_factory(f"tone-{n}.wav", frequency=220 + 100 * n) for n in range(3)]
    collections = []
    for n, selected in enumerate([paths[:2], paths[1:]]):
        job = submit_collection(application, selected, key=f"collection-{n}", name=f"Set {n}")
        completed = await execute(application, job["job_id"])
        assert completed["outcome"] == "complete"
        collections.append(completed["result"]["collection_id"])
    return paths, collections


def request(collections, **changes):
    return DeliveryRequest(
        **{
            "name": "Synthetic Serato trial",
            "collection_ids": collections,
            "workflow": "serato_portable",
            "app_version": "test-version",
            "audio_mode": "preserve",
            "pilot_size": 3,
            **changes,
        }
    )


async def prepared_delivery(application, audio_factory, **request_changes):
    paths, collections = await catalog(application, audio_factory)
    status = delivery.create_delivery(application, request(collections, **request_changes))
    job = delivery.prepare_delivery(
        application, status["delivery_id"], status["revision"], "prepare"
    )
    completed = await execute(application, job["job_id"])
    assert completed["outcome"] == "complete"
    manifest = json.loads(Path(completed["result"]["manifest_path"]).read_text())
    return (
        paths,
        collections,
        delivery.delivery_status(application, status["delivery_id"]),
        manifest,
    )


def observation(status, manifest, stage="imported", **changes):
    return DeliveryObservation(
        **{
            "revision": status["revision"],
            "stage": stage,
            "app_version": status["request"]["app_version"],
            "track_count": manifest["unique_track_count"],
            "playlist_counts": manifest["playlist_counts"],
            "checked_recording_ids": [t["recording_id"] for t in manifest["tracks"]],
            "observer": "Synthetic-test operator",
            "notes": "Explicit simulated observation; no real DJ app or device is involved.",
            "method": "physical_hardware" if stage == "hardware_playback" else "native_app_ui",
            "outcome": "passed",
            **changes,
        }
    )


def observe(application, status, manifest, stage="imported", **changes):
    return delivery.observe_delivery(
        application, status["delivery_id"], observation(status, manifest, stage, **changes)
    )


@pytest.fixture
def simulated_device(monkeypatch, tmp_path):
    """Do not bind or inspect an actual user volume in workflow tests."""
    root = tmp_path / "simulated-usb"
    root.mkdir()

    def inspect(path, expected_hashes=None):
        return {
            "path": str(root),
            "is_mount_point": True,
            "volume_identity": {
                "value": "synthetic-volume-identity",
                "confidence": "strong",
                "filesystem": "fat32",
                "partition_scheme": "MBR",
            },
            "volume_unchanged": True,
            "status": "verified_hashes" if expected_hashes else "inventory_only",
            "hash_inventory": {"matched": expected_hashes or [], "missing": []},
            "native_markers": {
                name: {
                    "present": True,
                    "evidence": "existence_only",
                    "contents_verified": False,
                }
                for name in ["serato", "device_library", "onelibrary"]
            },
            "native_export_verified": False,
            "hardware_verified": False,
            "writes_performed": False,
        }

    monkeypatch.setattr(delivery, "inspect_device", inspect)
    return str(root)


async def test_multicollection_preparation_preserves_ids_membership_and_sources(
    application, audio_factory, monkeypatch, media_dependencies
):
    paths, collections = await catalog(application, audio_factory)
    hashes = {p: checksum(p) for p in paths}
    status = delivery.create_delivery(application, request(collections))
    snapshot = status["snapshot"]
    assert snapshot["selected_unique_tracks"] == 3
    assert snapshot["membership_count"] == 4
    ids = [t["recording_id"] for t in snapshot["tracks"]]
    frozen_title = snapshot["tracks"][0]["title"]
    with application.db.transaction() as session:
        # Simulate a later catalog label correction: this delivery keeps its frozen labels/IDs.
        session.get(Recording, ids[0]).title = "Later catalog label"
        before = (
            session.scalar(select(func.count()).select_from(Recording)),
            session.scalar(select(func.count()).select_from(AssetRevision)),
        )

    async def no_reingestion(*args):
        pytest.fail("Delivery must consume stable catalog IDs, not reparse a new collection")

    monkeypatch.setattr(Worker, "ingest", no_reingestion)
    job = delivery.prepare_delivery(application, status["delivery_id"], 1, "stable-prepare")
    assert (
        delivery.prepare_delivery(application, status["delivery_id"], 1, "stable-prepare")["job_id"]
        == job["job_id"]
    )
    completed = await execute(application, job["job_id"])
    assert completed["counts"] == {"succeeded": 3}
    manifest = json.loads(Path(completed["result"]["manifest_path"]).read_text())
    assert [t["recording_id"] for t in manifest["tracks"]] == ids
    assert manifest["tracks"][0]["title"] == frozen_title
    assert manifest["playlist_counts"] == dict.fromkeys(collections, 2)
    assert len({t["path"] for t in manifest["tracks"]}) == 3
    for track in manifest["tracks"]:
        assert Path(track["path"]) != Path(track["source_path"])
        assert track["source_sha256"] == hashes[Path(track["source_path"])]
        assert pcm_hash(Path(track["path"])) == pcm_hash(Path(track["source_path"]))
    assert {p: checksum(p) for p in paths} == hashes
    with application.db.transaction() as session:
        after = (
            session.scalar(select(func.count()).select_from(Recording)),
            session.scalar(select(func.count()).select_from(AssetRevision)),
        )
        assert session.get(Recording, ids[0]).title == "Later catalog label"
    assert after == before
    status = delivery.delivery_status(application, status["delivery_id"])
    assert status["prepared_for_import"] and not status["ready_for_departure"]


async def test_pilot_samples_multiple_collections(application, audio_factory):
    paths, collections = await catalog(application, audio_factory)
    status = delivery.create_delivery(
        application, request(list(reversed(collections)), pilot_size=2)
    )
    selected = status["snapshot"]["tracks"]
    assert [t["path"] for t in selected] == [str(paths[1]), str(paths[0])]
    assert [len(c["tracks"]) for c in status["snapshot"]["collections"]] == [1, 2]


async def test_conflicting_byte_revisions_of_same_recording_are_rejected(
    application, audio_factory
):
    collections = []
    for n in range(2):
        path = audio_factory(f"edition-{n}.wav", frequency=220 + 100 * n)
        plan = application.plan(
            CollectionRequest(
                name=f"Different bytes {n}",
                tracks=[TrackInput(path=str(path), artist="Test Artist", title="Same recording")],
            )
        )
        job = application.start(
            StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key=f"edition-{n}")
        )
        completed = await execute(application, job["job_id"])
        assert completed["outcome"] == "complete"
        collections.append(completed["result"]["collection_id"])
    first, second = [application.collection(cid)["tracks"][0] for cid in collections]
    assert first["recording_id"] == second["recording_id"]
    assert first["asset_revision_id"] != second["asset_revision_id"]
    with pytest.raises(AppError) as error:
        delivery.create_delivery(application, request(collections))
    assert error.value.code == "DELIVERY_REVISION_CONFLICT"


async def test_full_preparation_requires_matching_completed_native_pilot(
    application, audio_factory, simulated_device, media_dependencies
):
    _, collections, pilot, manifest = await prepared_delivery(application, audio_factory)
    full = delivery.create_delivery(
        application, request(collections, phase="full", pilot_delivery_id=pilot["delivery_id"])
    )
    with pytest.raises(AppError, match="native-app/device trial") as error:
        delivery.prepare_delivery(application, full["delivery_id"], 1, "full-before-trial")
    assert error.value.code == "PILOT_REQUIRED"
    pilot = delivery.bind_device(
        application, pilot["delivery_id"], pilot["revision"], simulated_device
    )
    for stage in ["imported", "analyzed", "native_exported", "device_library_checked"]:
        pilot = observe(application, pilot, manifest, stage)
    pilot = delivery.verify_device(application, pilot["delivery_id"], pilot["revision"])
    pilot = observe(application, pilot, manifest, "hardware_playback")
    assert pilot["requirements_met_at_last_check"] and not pilot["hardware_verified_automatically"]
    pilot = delivery.verify_device(application, pilot["delivery_id"], pilot["revision"])
    assert pilot["ready_for_departure"]
    job = delivery.prepare_delivery(application, full["delivery_id"], 1, "full-after-trial")
    assert (await execute(application, job["job_id"]))["outcome"] == "complete"
    mismatched = delivery.create_delivery(
        application,
        request(
            collections, phase="full", pilot_delivery_id=pilot["delivery_id"], audio_mode="mp3_320"
        ),
    )
    with pytest.raises(AppError) as error:
        delivery.prepare_delivery(application, mismatched["delivery_id"], 1, "wrong-mode")
    assert error.value.code == "PILOT_REQUIRED"


@pytest.mark.parametrize(
    "workflow", ["rekordbox_usb", "serato_portable", "rekordbox_import", "serato_import"]
)
async def test_full_local_preparation_without_pilot_never_grants_native_readiness(
    application, audio_factory, monkeypatch, media_dependencies, workflow
):
    _, collections = await catalog(application, audio_factory)
    monkeypatch.setattr(
        delivery, "inspect_device", lambda *_: pytest.fail("Local preparation must not query USB")
    )
    status = delivery.create_delivery(
        application,
        request(
            collections,
            phase="full",
            pilot_size=1,
            workflow=workflow,
            hardware_profile="cdj-3000" if workflow == "rekordbox_usb" else None,
        ),
    )
    assert status["snapshot"]["selected_unique_tracks"] == 3
    assert status["pilot_validation"] == "not_supplied"
    assert not status["pilot_required_before_full"]
    job = delivery.prepare_delivery(application, status["delivery_id"], 1, "full-at-home")
    completed = await execute(application, job["job_id"])
    assert completed["outcome"] == "complete"
    manifest = json.loads(Path(completed["result"]["manifest_path"]).read_text())
    assert manifest["preparation_scope"] == "local_files_only"
    assert manifest["pilot_validation"] == "not_supplied"
    status = delivery.delivery_status(application, status["delivery_id"])
    assert status["prepared_for_import"] and status["prepared_without_validated_pilot"]
    assert not status["ready_for_app_use"] and not status["ready_for_departure"]
    assert "imported" in status["blockers"] and "analyzed" in status["blockers"]
    with pytest.raises(AppError) as error:
        if workflow.endswith("_import"):
            delivery.verify_app(application, status["delivery_id"], status["revision"])
        else:
            delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert error.value.code in {"APP_STAGE_REQUIRED", "NATIVE_EXPORT_REQUIRED"}


async def test_nonexistent_or_nonpilot_claim_is_rejected(application, audio_factory):
    _, collections = await catalog(application, audio_factory)
    full = delivery.create_delivery(application, request(collections, phase="full"))
    for claimed in ("delivery_missing", full["delivery_id"]):
        status = delivery.create_delivery(
            application, request(collections, phase="full", pilot_delivery_id=claimed)
        )
        assert status["pilot_validation"] == "not_validated"
        with pytest.raises(AppError) as error:
            delivery.prepare_delivery(application, status["delivery_id"], 1, f"invalid-{claimed}")
        assert error.value.code == "PILOT_REQUIRED"


async def test_delivery_unavailable_selected_location_is_actionable(
    application, audio_factory, monkeypatch
):
    _, collections = await catalog(application, audio_factory)
    original = application.collection

    def unavailable(collection_id):
        snapshot = original(collection_id)
        for track in snapshot["tracks"]:
            track["path"] = None
        return snapshot

    monkeypatch.setattr(application, "collection", unavailable)
    with pytest.raises(AppError, match="reconcile") as error:
        delivery.create_delivery(application, request(collections))
    assert error.value.code == "FILE_UNAVAILABLE"


@pytest.mark.parametrize("partition", ["MBR", "GPT", None])
async def test_windows_partition_evidence_reaches_actual_departure_gate(
    application, audio_factory, monkeypatch, tmp_path, media_dependencies, partition
):
    _, _, status, manifest = await prepared_delivery(
        application, audio_factory, workflow="rekordbox_usb", hardware_profile="cdj-3000"
    )
    root = tmp_path / "windows-volume-fixture"
    root.mkdir()
    marker = root / "PIONEER/rekordbox/export.pdb"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"synthetic marker only; no native database written")
    monkeypatch.setattr(Path, "is_mount", lambda self: self == root)
    monkeypatch.setattr(device_readback.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        device_readback,
        "_windows_volume_info",
        lambda _: {
            "value": "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\",
            "method": "windows_volume_guid",
            "confidence": "strong",
            "filesystem": "FAT32",
            "filesystem_type": "FAT32",
        },
    )

    def scheme(_):
        if partition is None:
            raise OSError("Partition query unavailable")
        return partition

    monkeypatch.setattr(device_readback, "_windows_partition_scheme", scheme)
    status = delivery.bind_device(application, status["delivery_id"], status["revision"], str(root))
    for stage in ("imported", "analyzed", "native_exported", "device_library_checked"):
        status = observe(application, status, manifest, stage)
    for track in status["evidence"]["analyzed"]["assets"]:
        shutil.copyfile(track["path"], root / f"{track['recording_id']}.wav")
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert not status["ready_for_departure"] and "hardware_playback" in status["blockers"]
    status = observe(
        application,
        status,
        manifest,
        "hardware_playback",
        hardware_profile="cdj-3000",
        firmware_version="3.20",
        storage_recognized=True,
    )
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert status["ready_for_departure"] is (partition == "MBR")
    assert status["current_device"]["volume_identity"]["partition_scheme"] == partition
    if partition != "MBR":
        assert (
            "partition_scheme_unknown" if partition is None else "partition_scheme_unsupported"
        ) in status["blockers"]


async def test_failed_preparation_retains_successes_but_publishes_no_handoff(
    application, audio_factory, media_dependencies
):
    paths, collections = await catalog(application, audio_factory)
    status = delivery.create_delivery(application, request(collections))
    paths[1].write_bytes(b"corrupted generated test audio")
    job = delivery.prepare_delivery(application, status["delivery_id"], 1, "partial")
    completed = await execute(application, job["job_id"])
    assert completed["outcome"] == "completed_with_gaps"
    assert completed["counts"] == {"succeeded": 2, "failed": 1}
    export_dir = application.workspace.exports / job["job_id"]
    assert not list(export_dir.glob("*.m3u8"))
    assert not (export_dir / "delivery-manifest.json").exists()
    status = delivery.delivery_status(application, status["delivery_id"])
    assert not status["prepared_for_import"] and not status["ready_for_departure"]
    fake_manifest = {
        "unique_track_count": 3,
        "playlist_counts": dict.fromkeys(collections, 2),
        "tracks": status["snapshot"]["tracks"],
    }
    with pytest.raises(AppError) as error:
        observe(application, status, fake_manifest)
    assert error.value.code == "PREPARATION_REQUIRED"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"stage": "analyzed"}, "STAGE_REQUIRED"),
        ({"track_count": 1}, "COVERAGE_MISMATCH"),
        ({"playlist_counts": {}}, "COVERAGE_MISMATCH"),
        ({"checked_recording_ids": ["not-a-frozen-recording"]}, "COVERAGE_MISMATCH"),
        ({"method": "physical_hardware"}, "EVIDENCE_METHOD"),
        ({"app_version": "different-version"}, "APP_VERSION_CHANGED"),
    ],
)
async def test_observations_require_order_exact_coverage_and_evidence_method(
    application, audio_factory, media_dependencies, changes, code
):
    _, _, status, manifest = await prepared_delivery(application, audio_factory)
    with pytest.raises(AppError) as error:
        observe(application, status, manifest, **changes)
    assert error.value.code == code
    assert delivery.delivery_status(application, status["delivery_id"])["revision"] == 1


@pytest.mark.parametrize("later_change", ["tags", "missing"])
async def test_native_tag_analysis_preserves_pcm_and_rejects_later_changes(
    application, audio_factory, simulated_device, media_dependencies, later_change
):
    sources, _, status, manifest = await prepared_delivery(application, audio_factory)
    original_hashes = [checksum(p) for p in sources]
    status = delivery.bind_device(application, status["delivery_id"], 1, simulated_device)
    status = observe(application, status, manifest)
    working = Path(manifest["tracks"][0]["path"])
    tags = WAVE(working)
    tags.tags.add(TBPM(encoding=3, text="128"))
    tags.tags.add(TKEY(encoding=3, text="Am"))
    tags.save()
    assert checksum(working) != manifest["tracks"][0]["sha256"]
    status = observe(application, status, manifest, "analyzed")
    assert status["evidence"]["analyzed"]["assets"][0]["sha256"] == checksum(working)
    assert [checksum(p) for p in sources] == original_hashes
    for stage in ["native_exported", "device_library_checked"]:
        status = observe(application, status, manifest, stage)
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    status = observe(application, status, manifest, "hardware_playback")
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert status["ready_for_departure"]
    if later_change == "tags":
        tags.tags.add(TBPM(encoding=3, text="130"))
        tags.save()
    else:
        working.unlink()
    with pytest.raises(AppError) as error:
        observe(application, status, manifest, "native_exported")
    if later_change == "tags":
        assert error.value.code == "ANALYSIS_CHANGED"
    invalidated = delivery.delivery_status(application, status["delivery_id"])
    assert invalidated["evidence"].get("analyzed", {}).get("outcome") != "passed"
    assert invalidated["revision"] > status["revision"]
    for stage in ["native_exported", "device_library_checked", "hardware_playback"]:
        assert invalidated["evidence"].get(stage, {}).get("outcome") != "passed"
    assert "readback" not in invalidated["evidence"]
    assert not invalidated["ready_for_departure"]


async def test_replaced_working_audio_cannot_be_recorded_as_native_analysis(
    application, audio_factory, simulated_device, media_dependencies
):
    _, _, status, manifest = await prepared_delivery(application, audio_factory)
    status = delivery.bind_device(application, status["delivery_id"], 1, simulated_device)
    for stage in ["imported", "analyzed", "native_exported"]:
        status = observe(application, status, manifest, stage)
    different_audio = audio_factory("replacement.wav", frequency=900)
    shutil.copyfile(different_audio, manifest["tracks"][0]["path"])
    with pytest.raises(AppError) as error:
        observe(application, status, manifest, "analyzed")
    assert error.value.code == "AUDIO_CHANGED"
    current = delivery.delivery_status(application, status["delivery_id"])
    assert current["evidence"].get("analyzed", {}).get("outcome") != "passed"
    assert current["evidence"].get("native_exported", {}).get("outcome") != "passed"
    assert current["revision"] > status["revision"] and not current["ready_for_departure"]


async def test_rekordbox_hardware_observation_requires_actual_target_and_supported_firmware(
    application, audio_factory, simulated_device, media_dependencies
):
    _, _, status, manifest = await prepared_delivery(
        application, audio_factory, workflow="rekordbox_usb", hardware_profile="cdj-3000"
    )
    status = delivery.bind_device(application, status["delivery_id"], 1, simulated_device)
    for stage in ["imported", "analyzed", "native_exported", "device_library_checked"]:
        status = observe(application, status, manifest, stage)
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    details = {
        "hardware_profile": "cdj-3000",
        "firmware_version": "3.31",
        "storage_recognized": True,
    }
    for missing in [
        {"hardware_profile": None},
        {"hardware_profile": "cdj-2000nxs"},
        {"firmware_version": None},
        {"storage_recognized": None},
        {"storage_recognized": False},
    ]:
        with pytest.raises(AppError) as error:
            observe(application, status, manifest, "hardware_playback", **{**details, **missing})
        assert error.value.code == "HARDWARE_DETAILS_REQUIRED"
    with pytest.raises(ValidationError):
        observation(status, manifest, "hardware_playback", **{**details, "firmware_version": " "})
    for withdrawn in ["3.30", "v3.30", " 3.30 "]:
        with pytest.raises(AppError) as error:
            observe(
                application,
                status,
                manifest,
                "hardware_playback",
                **{**details, "firmware_version": withdrawn},
            )
        assert error.value.code == "FIRMWARE_UNSUPPORTED"
    status = observe(application, status, manifest, "hardware_playback", **details)
    recorded = status["evidence"]["hardware_playback"]
    assert all(recorded[key] == value for key, value in details.items())
    assert not status["hardware_verified_automatically"]
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert status["ready_for_departure"]


async def test_changed_manifest_cannot_redefine_native_coverage(
    application, audio_factory, media_dependencies
):
    _, _, status, manifest = await prepared_delivery(application, audio_factory)
    path = Path(status["preparation_job"]["result"]["manifest_path"])
    altered = {**manifest, "unique_track_count": 1, "tracks": manifest["tracks"][:1]}
    path.write_text(json.dumps(altered))
    with pytest.raises(AppError) as error:
        observe(application, status, altered)
    assert error.value.code == "MANIFEST_CHANGED"
    assert delivery.delivery_status(application, status["delivery_id"])["revision"] == 1


async def test_device_markers_and_hash_readback_do_not_skip_native_or_hardware_evidence(
    application, audio_factory, simulated_device, media_dependencies
):
    _, _, status, manifest = await prepared_delivery(application, audio_factory)
    status = delivery.bind_device(application, status["delivery_id"], 1, simulated_device)
    assert status["evidence"]["device"]["native_markers"]["serato"]["present"]
    assert not status["ready_for_departure"]
    with pytest.raises(AppError) as error:
        delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert error.value.code == "NATIVE_EXPORT_REQUIRED"
    for stage in ["imported", "analyzed", "native_exported"]:
        status = observe(application, status, manifest, stage)
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert not status["ready_for_departure"]
    assert "device_library_checked" in status["blockers"]
    assert "hardware_playback" in status["blockers"]
    status = observe(application, status, manifest, "device_library_checked")
    assert not status["ready_for_departure"]
    with pytest.raises(AppError) as error:
        observe(
            application,
            status,
            manifest,
            "hardware_playback",
            checked_recording_ids=[manifest["tracks"][0]["recording_id"]],
        )
    assert error.value.code == "COVERAGE_MISMATCH"  # Every small-pilot track needs playback.
    status = observe(application, status, manifest, "hardware_playback")
    assert not status["ready_for_departure"] and status["requirements_met_at_last_check"]
    status = delivery.verify_device(application, status["delivery_id"], status["revision"])
    assert status["ready_for_departure"] and not status["native_automation_available"]
    read_only = delivery.delivery_status(application, status["delivery_id"])
    assert not read_only["ready_for_departure"] and not read_only["hashes_rechecked_by_status"]
    stale = observation(status, manifest)
    status = observe(
        application,
        status,
        manifest,
        "imported",
        outcome="failed",
        track_count=0,
        playlist_counts={},
        checked_recording_ids=[],
    )
    assert not status["ready_for_departure"]
    assert not any(k in status["evidence"] for k in ["analyzed", "native_exported", "readback"])
    with pytest.raises(AppError) as error:
        delivery.observe_delivery(application, status["delivery_id"], stale)
    assert error.value.code == "DELIVERY_STALE"
