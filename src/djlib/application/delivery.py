"""Persist an app-mediated DJ delivery, with explicit evidence for every boundary.

This module never writes a USB or a native DJ database. Native actions belong to
rekordbox/Serato; observations are labeled operator evidence, not inferred success.
"""

import asyncio
from collections import Counter
from pathlib import Path

from sqlalchemy import select, update

from djlib.application.service import add_event, new_id, require
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.contracts import DeliveryObservation, DeliveryRequest
from djlib.domain.errors import AppError
from djlib.exporting.delivery_media import pcm_hash, prepare_media, safe_name
from djlib.exporting.device_readback import inspect_device
from djlib.exporting.handoff import atomic_text
from djlib.exporting.targets import assess_track, target_profiles
from djlib.persistence.models import Delivery, Job, JobItem, timestamp
from djlib.workspace import atomic_json

STAGES = ("imported", "analyzed", "native_exported", "device_library_checked", "hardware_playback")


def _load(app, delivery_id):
    with app.db.transaction() as session:
        d = require(session, Delivery, delivery_id)
        return {
            "delivery_id": d.id,
            "revision": d.revision,
            "request": d.request,
            "snapshot": d.snapshot,
            "evidence": d.evidence,
            "job_id": d.job_id,
            "created_at": d.created_at,
        }


def _revision(d, revision):
    if d["revision"] != revision:
        raise AppError(
            "DELIVERY_STALE", "Read the current delivery revision before recording evidence.", 409
        )


def _save_evidence(app, d, evidence):
    with app.db.transaction() as session:
        result = session.execute(
            update(Delivery)
            .where(Delivery.id == d["delivery_id"], Delivery.revision == d["revision"])
            .values(evidence=evidence, revision=d["revision"] + 1, updated_at=timestamp())
        )
        if result.rowcount != 1:
            raise AppError("DELIVERY_STALE", "The delivery evidence changed; read it again.", 409)


def create_delivery(app, request: DeliveryRequest):
    if request.workflow == "rekordbox_usb" and request.hardware_profile not in target_profiles():
        raise AppError("TARGET_UNKNOWN", "Choose a documented target from delivery targets.")
    snapshots = [app.collection(cid) for cid in request.collection_ids]
    unique = {}
    # A pilot samples collections in round-robin order, not just the first playlist.
    for index in range(max((len(s["tracks"]) for s in snapshots), default=0)):
        for snapshot in snapshots:
            if index < len(snapshot["tracks"]):
                t = snapshot["tracks"][index]
                existing = unique.get(t["recording_id"])
                if existing and existing["asset_revision_id"] != t["asset_revision_id"]:
                    raise AppError(
                        "DELIVERY_REVISION_CONFLICT",
                        "Selected collections use different byte revisions for one recording; "
                        "choose the intended revision before delivery.",
                    )
                unique.setdefault(t["recording_id"], t)
                if len(unique) > 10_000:
                    raise AppError(
                        "ITEM_LIMIT", "A delivery is limited to 10,000 unique recordings."
                    )
    if not unique:
        raise AppError("COLLECTION_EMPTY", "Prepare a delivery from accepted catalog recordings.")
    all_tracks = list(unique.values())
    selected = all_tracks[: request.pilot_size] if request.phase == "pilot" else all_tracks
    chosen = {t["recording_id"] for t in selected}
    for s in snapshots:
        s["tracks"] = [t for t in s["tracks"] if t["recording_id"] in chosen]
    snapshot = {
        "collections": snapshots,
        "tracks": selected,
        "requested_unique_tracks": len(all_tracks),
        "selected_unique_tracks": len(selected),
        "membership_count": sum(len(s["tracks"]) for s in snapshots),
        "frozen_at": timestamp(),
    }
    with app.db.transaction() as session:
        d = Delivery(
            id=new_id("delivery"), request=request.model_dump(mode="json"), snapshot=snapshot
        )
        session.add(d)
        session.flush()
        delivery_id = d.id
    return delivery_status(app, delivery_id)


def _pilot_passed(app, d):
    request = d["request"]
    if request["phase"] == "pilot":
        return True
    pilot_id = request.get("pilot_delivery_id")
    if not pilot_id:
        return False
    pilot = _load(app, pilot_id)
    if pilot["request"]["phase"] != "pilot":
        return False
    for key in ("workflow", "hardware_profile", "audio_mode", "app_version"):
        if pilot["request"].get(key) != request.get(key):
            return False
    return delivery_status(app, pilot_id)["requirements_met_at_last_check"]


def prepare_delivery(app, delivery_id, revision, key):
    d = _load(app, delivery_id)
    _revision(d, revision)
    if not d["job_id"] and not _pilot_passed(app, d):
        raise AppError(
            "PILOT_REQUIRED",
            "Complete a matching small native-app/device trial before full preparation.",
        )
    payload = {
        "delivery_id": delivery_id,
        "target": d["request"],
        "snapshot": d["snapshot"],
        "tracks": d["snapshot"]["tracks"],
    }
    job = app.submit("delivery", payload, key)
    return job


def _active(app, job_id, generation):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        return job.state == "running" and job.generation == generation


async def prepare_item(app, job_id, generation, item_id):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        item = require(session, JobItem, item_id)
        item.state = "running"
        track, request = item.request["track"], job.request["target"]
    try:
        source = app.workspace.authorize(track["path"])
        directory = app.workspace.exports / job_id / "media" / item_id / str(generation)
        result = await asyncio.to_thread(
            prepare_media, source, track, directory, request["audio_mode"]
        )
        if request["workflow"] == "rekordbox_usb":
            problems = assess_track(
                request["hardware_profile"],
                result["properties"],
                file_extension=Path(result["path"]).suffix,
            )
            if problems:
                raise AppError(
                    "TARGET_AUDIO_INCOMPATIBLE",
                    "Prepared audio does not meet the selected player profile: "
                    + ", ".join(problems),
                )
        if not _active(app, job_id, generation):
            return
        with app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state != "running" or job.generation != generation:
                return
            item = require(session, JobItem, item_id)
            item.state, item.result = "succeeded", result
            add_event(
                session, require(session, Job, job_id), "delivery_item_ready", {"item_id": item_id}
            )
    except (AppError, OSError) as exc:
        if not _active(app, job_id, generation):
            return
        error = (
            exc.as_dict()
            if isinstance(exc, AppError)
            else AppError("PREPARATION_FAILED", "Working-copy preparation failed.").as_dict()
        )
        with app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state != "running" or job.generation != generation:
                return
            item = require(session, JobItem, item_id)
            item.state, item.result = "failed", {"error": error}
            add_event(
                session,
                require(session, Job, job_id),
                "delivery_item_failed",
                {"item_id": item_id, "error": error},
            )


def _steps(d):
    if d["request"]["workflow"] == "rekordbox_usb":
        profile = target_profiles()[d["request"]["hardware_profile"]]
        library = profile.get("library_format", "the player-compatible device library")
        return [
            "In rekordbox EXPORT mode: File > Import > Import Playlist; import each prepared M3U8.",
            (
                "Confirm exact playlist membership; analyze new working copies for "
                "BPM/Grid and Key. Audition grids and cue points; do not reanalyze "
                "existing edited tracks."
            ),
            (
                f"Export these playlists through rekordbox Devices to the bound USB using "
                f"{library}. Do not substitute a Finder copy or M3U/XML for native "
                f"export."
            ),
            (
                "Wait for native export completion. Browse the USB in rekordbox, inspect "
                "playlist counts and load the trial tracks. Do not convert an existing "
                "device library without preserving it."
            ),
            (
                "Run delivery verify-device. Eject through rekordbox; load and play "
                "trial tracks on the specified player, then record the actual result."
            ),
        ]
    return [
        (
            "In Serato Files, import the prepared working files and create regular "
            "crates matching the supplied playlist membership; Smart Crates are not "
            "portable crates."
        ),
        (
            "Analyze BPM, Key and beatgrids, then inspect and load the tracks. Serato "
            "3.3.5 and earlier require disconnected DJ hardware for analysis; check the "
            "installed version's instructions."
        ),
        (
            "In Serato Files, drag each regular crate to the bound USB and choose Copy. "
            "This transfers media and crates; an operating-system file copy does not do "
            "this."
        ),
        (
            "Wait for completion; reconnect and inspect crates and tracks in Serato, "
            "preferably on the destination computer. Run delivery verify-device and test "
            "playback there."
        ),
        (
            "This prepares a portable Serato library for another Serato computer, not a "
            "standalone rekordbox player USB."
        ),
    ]


async def finish_preparation(app, job_id, generation):
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        items = list(
            session.scalars(
                select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.position)
            )
        )
        counts = Counter(i.state for i in items)
        tracks = [dict(i.result) for i in items if i.state == "succeeded"]
        delivery_id = job.request["delivery_id"]
    if not _active(app, job_id, generation):
        return
    if counts["failed"] or counts["pending"] or counts["running"]:
        with app.db.transaction() as session:
            job = require(session, Job, job_id)
            if job.state != "running" or job.generation != generation:
                return
            job.state, job.outcome = "completed", "completed_with_gaps"
            job.result = {
                "delivery_id": delivery_id,
                "prepared_count": len(tracks),
                "app_state": "not_imported",
                "device_state": "not_exported",
                "blocked_by": ["Resolve failed preparation items before native import."],
            }
            add_event(session, job, "delivery_preparation_incomplete", dict(counts))
        return
    # A pause after an item boundary must not publish a complete manifest.
    d = _load(app, delivery_id)
    by_id = {t["recording_id"]: t for t in tracks}
    for track in tracks:
        observed = await asyncio.to_thread(checksum, app.workspace.authorize(track["path"]))
        if observed != track["sha256"]:
            raise AppError(
                "WORKING_COPY_CHANGED", "A working copy changed before preparation completed."
            )
        if not _active(app, job_id, generation):
            return
    directory = app.workspace.exports / job_id
    playlists, expected = [], {}
    for number, snapshot in enumerate(d["snapshot"]["collections"], 1):
        selected = [by_id[t["recording_id"]] for t in snapshot["tracks"]]
        if not selected:
            continue
        name = f"{number:02d} - {safe_name(snapshot['name'])}.m3u8"
        path = directory / name
        atomic_text(
            path,
            "#EXTM3U\n"
            + "".join(f"#EXTINF:-1,{t['artist']} - {t['title']}\n{t['path']}\n" for t in selected),
        )
        expected[snapshot["collection_id"]] = len(selected)
        playlists.append(
            {
                "collection_id": snapshot["collection_id"],
                "name": snapshot["name"],
                "path": str(path),
                "track_count": len(selected),
            }
        )
    manifest = {
        "schema_version": "1",
        "delivery_id": delivery_id,
        "snapshot": d["snapshot"],
        "target": d["request"],
        "tracks": tracks,
        "playlists": playlists,
        "playlist_counts": expected,
        "unique_track_count": len(tracks),
        "native_steps": _steps(d),
        "app_state": "prepared_for_import",
        "device_state": "not_exported",
    }
    atomic_json(directory / "delivery-manifest.json", manifest)
    atomic_text(directory / "NATIVE_STEPS.txt", "\n\n".join(_steps(d)) + "\n")
    with app.db.transaction() as session:
        job = require(session, Job, job_id)
        if job.state != "running" or job.generation != generation:
            return
        job.state, job.outcome = "completed", "complete"
        job.result = {
            "delivery_id": delivery_id,
            "manifest_path": str(directory / "delivery-manifest.json"),
            "manifest_sha256": checksum(directory / "delivery-manifest.json"),
            "prepared_count": len(tracks),
            "playlists": playlists,
            "app_state": "prepared_for_import",
            "device_state": "not_exported",
        }
        add_event(session, job, "delivery_prepared", {"track_count": len(tracks)})


def _prepared(app, d):
    if not d["job_id"]:
        raise AppError("PREPARATION_REQUIRED", "Prepare the delivery before native app work.")
    job = app.job(d["job_id"])
    if job["state"] != "completed" or job["outcome"] != "complete":
        raise AppError(
            "PREPARATION_REQUIRED", "All frozen delivery tracks must finish preparation."
        )
    import json

    path = app.workspace.authorize(job["result"]["manifest_path"])
    if checksum(path) != job["result"]["manifest_sha256"]:
        raise AppError("MANIFEST_CHANGED", "The frozen delivery manifest changed.")
    return json.loads(path.read_text(encoding="utf-8"))


def bind_device(app, delivery_id, revision, path):
    d = _load(app, delivery_id)
    _revision(d, revision)
    report = inspect_device(path)
    if not report["is_mount_point"]:
        raise AppError(
            "DEVICE_NOT_MOUNTED", "Bind the mounted USB volume root, not an ordinary folder."
        )
    evidence = dict(d["evidence"])
    # Rebinding invalidates every device-dependent observation.
    for key in ("native_exported", "device_library_checked", "hardware_playback", "readback"):
        evidence.pop(key, None)
    evidence["device"] = report
    _save_evidence(app, d, evidence)
    return delivery_status(app, delivery_id)


def _same_volume(before, after):
    return (
        after.get("is_mount_point")
        and after.get("volume_unchanged") is True
        and before["volume_identity"]["confidence"] == "strong"
        and after["volume_identity"]["confidence"] == "strong"
        and before["volume_identity"]["value"] == after["volume_identity"]["value"]
    )


def _reconcile_analysis(app, manifest):
    result = []
    for track in manifest["tracks"]:
        path = app.workspace.authorize(track["path"])
        if checksum(path) == track["sha256"]:
            result.append(
                {
                    "recording_id": track["recording_id"],
                    "path": str(path),
                    "sha256": track["sha256"],
                    "pcm_sha256": track["pcm_sha256"],
                }
            )
            continue
        inspected = inspect_audio(path)
        measured = inspected.as_dict()
        if (
            any(
                measured.get(key) != track["properties"].get(key)
                for key in ("codec", "sample_rate", "channels", "bit_depth")
            )
            or abs(inspected.duration_seconds - track["properties"]["duration_seconds"]) > 0.01
        ):
            raise AppError(
                "AUDIO_CHANGED", "Working-copy stream properties changed; repeat preparation."
            )
        if pcm_hash(path) != track["pcm_sha256"]:
            raise AppError(
                "AUDIO_CHANGED", "Working-copy audio changed; rebuild and repeat native stages."
            )
        if checksum(path) != inspected.sha256:
            raise AppError(
                "FILE_CHANGED",
                "Working-copy tags changed during verification; wait for analysis to finish.",
            )
        result.append(
            {
                "recording_id": track["recording_id"],
                "path": str(path),
                "sha256": inspected.sha256,
                "pcm_sha256": track["pcm_sha256"],
            }
        )
    return result


def observe_delivery(app, delivery_id, observation: DeliveryObservation):
    d = _load(app, delivery_id)
    _revision(d, observation.revision)
    manifest = _prepared(app, d)
    if observation.app_version != d["request"]["app_version"]:
        raise AppError(
            "APP_VERSION_CHANGED", "Create a new target plan for a different app version."
        )
    if observation.outcome == "passed" and (
        observation.track_count != manifest["unique_track_count"]
        or observation.playlist_counts != manifest["playlist_counts"]
    ):
        raise AppError(
            "COVERAGE_MISMATCH", "Observed native counts must match the frozen manifest."
        )
    all_ids = {t["recording_id"] for t in manifest["tracks"]}
    checked = set(observation.checked_recording_ids)
    full_check = observation.stage != "hardware_playback" or d["request"]["phase"] == "pilot"
    if not checked <= all_ids or (
        observation.outcome == "passed" and (not checked or (full_check and checked != all_ids))
    ):
        raise AppError(
            "COVERAGE_MISMATCH",
            "List every checked recording ID; only hardware playback permits a sample.",
        )
    expected_method = (
        "physical_hardware" if observation.stage == "hardware_playback" else "native_app_ui"
    )
    if observation.method != expected_method:
        raise AppError("EVIDENCE_METHOD", "The observation method does not prove this stage.")
    if (
        observation.outcome == "passed"
        and observation.stage == "hardware_playback"
        and d["request"]["workflow"] == "rekordbox_usb"
    ):
        if (
            observation.hardware_profile != d["request"]["hardware_profile"]
            or not (observation.firmware_version or "").strip()
            or observation.storage_recognized is not True
        ):
            raise AppError(
                "HARDWARE_DETAILS_REQUIRED",
                "Record the actual player profile, firmware, "
                "and successful USB recognition with hardware playback.",
            )
        if (
            observation.hardware_profile == "cdj-3000"
            and observation.firmware_version.strip().lower().removeprefix("v").strip() == "3.30"
        ):
            raise AppError(
                "FIRMWARE_UNSUPPORTED",
                "CDJ-3000 firmware 3.30 was withdrawn; verify the player firmware before delivery.",
            )
    evidence = dict(d["evidence"])
    index = STAGES.index(observation.stage)
    if (
        observation.outcome == "passed"
        and index
        and evidence.get(STAGES[index - 1], {}).get("outcome") != "passed"
    ):
        raise AppError("STAGE_REQUIRED", "Complete and observe the preceding native stage first.")
    if observation.outcome == "passed" and index >= 2:
        bound = evidence.get("device")
        if not bound:
            raise AppError("DEVICE_REQUIRED", "Bind the intended mounted USB before native export.")
        if observation.stage != "hardware_playback" and not _same_volume(
            bound, inspect_device(bound["path"])
        ):
            raise AppError("DEVICE_CHANGED", "The bound USB is missing or has changed.")
    if (
        observation.outcome == "passed"
        and observation.stage == "hardware_playback"
        and not _readback_passed(evidence)
    ):
        raise AppError(
            "READBACK_REQUIRED", "Verify the bound device's prepared audio before hardware testing."
        )
    # New earlier observations invalidate later observations, even when marked failed.
    for later in STAGES[index + 1 :]:
        evidence.pop(later, None)
    if index <= STAGES.index("native_exported"):
        evidence.pop("readback", None)
    record = {
        **observation.model_dump(mode="json"),
        "observed_at": timestamp(),
        "evidence_level": "operator_reported",
        "delivery_id": delivery_id,
    }
    if observation.stage == "analyzed" and observation.outcome == "passed":
        try:
            record["assets"] = _reconcile_analysis(app, manifest)
        except (AppError, OSError) as exc:
            exc = (
                exc
                if isinstance(exc, AppError)
                else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
            )
            evidence[observation.stage] = {**record, "outcome": "failed", "error": exc.as_dict()}
            _save_evidence(app, d, evidence)
            raise exc
    if observation.stage == "native_exported" and observation.outcome == "passed":
        try:
            for t in evidence["analyzed"]["assets"]:
                if checksum(app.workspace.authorize(t["path"])) != t["sha256"]:
                    raise AppError(
                        "ANALYSIS_CHANGED",
                        "Working copies changed after analysis; record analysis again.",
                    )
        except (AppError, OSError) as exc:
            exc = (
                exc
                if isinstance(exc, AppError)
                else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
            )
            evidence["analyzed"] = {
                **evidence["analyzed"],
                "outcome": "failed",
                "error": exc.as_dict(),
            }
            evidence[observation.stage] = {**record, "outcome": "failed", "error": exc.as_dict()}
            _save_evidence(app, d, evidence)
            raise exc
    evidence[observation.stage] = record
    _save_evidence(app, d, evidence)
    return delivery_status(app, delivery_id)


def verify_device(app, delivery_id, revision):
    d = _load(app, delivery_id)
    _revision(d, revision)
    evidence = dict(d["evidence"])
    if evidence.get("native_exported", {}).get("outcome") != "passed":
        raise AppError(
            "NATIVE_EXPORT_REQUIRED", "Observe actual native export before device readback."
        )
    bound = evidence["device"]
    hashes = [t["sha256"] for t in evidence["analyzed"]["assets"]]
    try:
        report = inspect_device(bound["path"], hashes)
    except (AppError, OSError) as exc:
        report = {
            "status": "device_unavailable",
            "volume_unchanged": False,
            "error": exc.as_dict() if isinstance(exc, AppError) else {"code": "DEVICE_UNAVAILABLE"},
        }
    if not _same_volume(bound, report) and report.get("status") not in {
        "identity_unverified",
        "device_unavailable",
        "not_mounted",
    }:
        report["status"] = "volume_changed"
    if report.get("status") != "verified_hashes":
        evidence.pop("hardware_playback", None)
    evidence["readback"] = {
        **report,
        "checked_at": timestamp(),
        "evidence_level": "machine_hash_readback",
    }
    _save_evidence(app, d, evidence)
    return delivery_status(app, delivery_id, fresh_readback=report)


def _readback_passed(evidence):
    readback = evidence.get("readback", {})
    return readback.get("status") == "verified_hashes" and readback.get("volume_unchanged") is True


def delivery_status(app, delivery_id, *, fresh_readback=None):
    d = _load(app, delivery_id)
    evidence = d["evidence"]
    job = app.job(d["job_id"]) if d["job_id"] else None
    prepared = bool(job and job["state"] == "completed" and job["outcome"] == "complete")
    blockers = []
    if not prepared:
        blockers.append("prepare_working_copies")
    if not evidence.get("device"):
        blockers.append("bind_target_usb")
    for stage in STAGES:
        if evidence.get(stage, {}).get("outcome") != "passed":
            blockers.append(stage)
    if not _readback_passed(evidence):
        blockers.append("device_audio_hash_readback")
    current = None
    if evidence.get("device"):
        try:
            current = inspect_device(evidence["device"]["path"])
            if not _same_volume(evidence["device"], current):
                blockers.append("device_identity_not_current")
            if d["request"]["workflow"] == "rekordbox_usb":
                profile = target_profiles()[d["request"]["hardware_profile"]]
                info = current["volume_identity"]
                fs = (info.get("filesystem") or "").casefold()
                aliases = {
                    "ms-dos fat32": "fat32",
                    "ms-dos fat16": "fat16",
                    "hfs": "hfs+",
                    "journaled hfs+": "hfs+",
                    "hfs+": "hfs+",
                }
                fs = aliases.get(fs, fs)
                allowed = {f.casefold() for f in profile["filesystems"]}
                if fs not in allowed:
                    blockers.append("filesystem_unknown_or_unsupported")
                scheme = (info.get("partition_scheme") or "").upper()
                if "GUID" in scheme:
                    scheme = "GPT"
                if not scheme:
                    blockers.append("partition_scheme_unknown")
                elif scheme in profile.get("unsupported_partition_schemes", []):
                    blockers.append("partition_scheme_unsupported")
                marker = profile["library_format"]
            else:
                marker = "serato"
            if evidence.get("native_exported", {}).get("outcome") == "passed" and not current.get(
                "native_markers", {}
            ).get(marker, {}).get("present", False):
                blockers.append("native_library_marker_missing")
        except (AppError, OSError):
            current = {"available": False}
            blockers.append("bound_device_unavailable")
    return {
        **d,
        "preparation_job": job,
        "prepared_for_import": prepared,
        "current_device": current,
        "native_steps": _steps(d),
        "blockers": blockers,
        "requirements_met_at_last_check": not blockers,
        "ready_for_departure": not blockers
        and fresh_readback is not None
        and fresh_readback.get("status") == "verified_hashes",
        "readiness_basis": (
            "operator-reported native stages and hardware sample plus machine audio hashes"
        ),
        "hardware_verified_automatically": False,
        "native_automation_available": False,
        "last_audio_readback_at": evidence.get("readback", {}).get("checked_at"),
        "hashes_rechecked_by_status": False,
        "next_step": blockers[0]
        if blockers
        else ("eject_safely" if fresh_readback else "verify_device_before_departure"),
        "source_quality": "Source fidelity is unchanged by compatibility conversion.",
        "pilot_required_before_full": d["request"]["phase"] == "pilot",
    }
