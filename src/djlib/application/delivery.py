"""Persist an app-mediated DJ delivery, with explicit evidence for every boundary.

This module never writes a USB or a native DJ database. Native actions belong to
rekordbox/Serato; observations are labeled operator evidence, not inferred success.
"""

import asyncio
from collections import Counter
from pathlib import Path

from sqlalchemy import select, update

from djlib.application.service import add_event, new_id, require
from djlib.audio.file_identity import path_snapshot
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.contracts import DeliveryObservation, DeliveryRequest
from djlib.domain.errors import AppError
from djlib.exporting.app_targets import assess_app_track
from djlib.exporting.delivery_media import pcm_hash, prepare_media, safe_name
from djlib.exporting.device_readback import inspect_device
from djlib.exporting.handoff import atomic_text
from djlib.exporting.targets import assess_track, target_profiles
from djlib.persistence.models import Delivery, Job, JobItem, timestamp
from djlib.persistence.organization_models import RecordingAnnotation
from djlib.workspace import atomic_json

STAGES = ("imported", "analyzed", "native_exported", "device_library_checked", "hardware_playback")
APP_WORKFLOWS = frozenset({"rekordbox_import", "serato_import"})


def _app_only(d):
    return d["request"]["workflow"] in APP_WORKFLOWS


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


def _save_evidence(app, d, evidence, *, job_guard=None, job_error=None):
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        guarded = None
        if job_guard is not None:
            job_id, generation = job_guard
            guarded = require(session, Job, job_id)
            if guarded.state != "running" or guarded.generation != generation:
                raise AppError(
                    "JOB_STALE", "Verification was paused, cancelled, or superseded.", 409
                )
        result = session.execute(
            update(Delivery)
            .where(Delivery.id == d["delivery_id"], Delivery.revision == d["revision"])
            .values(evidence=evidence, revision=d["revision"] + 1, updated_at=timestamp())
        )
        if result.rowcount != 1:
            raise AppError("DELIVERY_STALE", "The delivery evidence changed; read it again.", 409)
        if guarded is not None:
            guarded.state = "failed" if job_error else "completed"
            guarded.outcome = None if job_error else "complete"
            guarded.result = {
                "delivery_id": d["delivery_id"],
                "revision": d["revision"] + 1,
                "evidence_committed": True,
                **({"error": job_error} if job_error else {}),
            }
            add_event(session, guarded, guarded.state, {"evidence_committed": True})


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
    if any(not isinstance(track.get("path"), str) or not track["path"] for track in selected):
        raise AppError(
            "FILE_UNAVAILABLE",
            "A selected collection recording has no available file location; reconcile its "
            "catalog location or rebuild the selection before preparing delivery.",
        )
    # Freeze explicit annotations with the selected byte revision. Native analysis
    # may replace working-copy tags, but never changes this provenance snapshot.
    annotations = {}
    with app.db.transaction() as session:
        ids = [t["asset_revision_id"] for t in selected]
        for start in range(0, len(ids), 500):
            for row in session.scalars(
                select(RecordingAnnotation).where(
                    RecordingAnnotation.asset_revision_id.in_(ids[start : start + 500])
                )
            ):
                annotations[row.asset_revision_id] = row
        for track in selected:
            row = annotations.get(track["asset_revision_id"])
            track["dj_metadata"] = {
                "annotation_revision": row.revision if row else 0,
                "annotations": dict(row.annotations) if row else {},
                "source": "explicit_catalog_annotation",
                "acoustic_analysis_performed": False,
            }
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
    pilot_id = request.get("pilot_delivery_id")
    if not pilot_id or request["phase"] != "full":
        return False
    try:
        pilot = _load(app, pilot_id)
    except AppError as exc:
        if exc.code == "NOT_FOUND":
            return False
        raise
    if pilot["request"]["phase"] != "pilot":
        return False
    for key in ("workflow", "hardware_profile", "audio_mode", "app_version"):
        if pilot["request"].get(key) != request.get(key):
            return False
    return delivery_status(app, pilot_id)["requirements_met_at_last_check"]


def prepare_delivery(app, delivery_id, revision, key):
    d = _load(app, delivery_id)
    _revision(d, revision)
    # Local preparation can happen at home before native or hardware validation.
    # A supplied pilot is an explicit claim: never silently accept a mismatched,
    # unavailable or incomplete trial. Omitting it does not grant native readiness.
    if not d["job_id"] and d["request"].get("pilot_delivery_id") and not _pilot_passed(app, d):
        raise AppError(
            "PILOT_REQUIRED",
            "The supplied pilot must be a completed matching native-app/device trial; "
            "omit its ID to prepare local files without claiming pilot validation.",
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
        else:
            app_name = "rekordbox" if request["workflow"] == "rekordbox_import" else "serato"
            problems = assess_app_track(app_name, result["properties"], Path(result["path"]).suffix)
            if problems:
                raise AppError(
                    "APP_AUDIO_OUTSIDE_SUBSET",
                    "Prepared audio is outside the native app input subset: " + ", ".join(problems),
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
    if _app_only(d):
        if d["request"]["workflow"] == "rekordbox_import":
            return [
                "In rekordbox: File > Import > Import Playlist; import each prepared M3U8.",
                "Confirm exact playlist membership and source paths. Analyze the new "
                "working copies for BPM/Grid and Key; inspect grids and audition cue points.",
                "Record imported and analyzed only after checking these tracks in the app. "
                "Keep working copies in place. No USB or player model is needed for app import.",
                "For a standalone player USB, create a separate rekordbox_usb delivery "
                "with its documented hardware profile and complete native device export.",
            ]
        return [
            "In Serato Files, import the prepared working copies and create regular "
            "crates matching the supplied playlist memberships.",
            "Analyze BPM, Key and beatgrids, then inspect and load the selected tracks. "
            "Use the analysis instructions for the installed Serato version.",
            "Record imported and analyzed only after checking these tracks in the app. "
            "Keep working copies in place. No USB or controller model is needed for app import.",
            "For a portable Serato USB library, create a separate serato_portable "
            "delivery and copy regular crates through the native Files panel.",
        ]
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
        "preparation_scope": "local_files_only",
        "pilot_validation": (
            "validated"
            if _pilot_passed(app, d)
            else "not_validated"
            if d["request"].get("pilot_delivery_id")
            else "not_supplied"
        ),
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


def inspect_native_xml(app, delivery_id, revision, path):
    """Compare a supported native XML snapshot; never advance a delivery stage."""
    from djlib.exporting.native_rekordbox import inspect_native_rekordbox

    d = _load(app, delivery_id)
    _revision(d, revision)
    if d["request"]["workflow"] not in {"rekordbox_import", "rekordbox_usb"}:
        raise AppError("WORKFLOW_SCOPE", "This snapshot inspection supports rekordbox deliveries.")
    report = inspect_native_rekordbox(app.workspace.authorize(path), _prepared(app, d))
    report["delivery_id"] = delivery_id
    report["delivery_revision"] = d["revision"]
    report["delivery_state_changed"] = False
    report["working_file_hashes_rechecked"] = False
    report["native_app_version_matches_request"] = (
        report["native_product_version"] == d["request"]["app_version"]
    )
    if not report["native_app_version_matches_request"]:
        report["status"] = "mismatch"
        report["errors"].append(
            {
                "code": "APP_VERSION_MISMATCH",
                "message": "Native snapshot version differs from the delivery's declared app "
                "version. Use the observed app version for a new trial.",
            }
        )
    return report


def bind_device(app, delivery_id, revision, path):
    d = _load(app, delivery_id)
    _revision(d, revision)
    if _app_only(d):
        raise AppError("WORKFLOW_SCOPE", "App-import deliveries do not require a USB device.")
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


def _working_signature(path):
    observed = path_snapshot(path)
    if not observed.is_regular or observed.is_reparse:
        raise AppError("FILE_CHANGED", "A working copy is no longer a regular audio file.")
    return {
        "identity": [v.hex() if isinstance(v, bytes) else v for v in observed.identity],
        "size": observed.size,
        "modified": observed.modified,
        "changed": observed.changed,
    }


def _reconcile_analysis_track(app, track):
    """Check one working copy; the signature fences deferred batch publication."""
    path = app.workspace.authorize(track["path"])
    signature = _working_signature(path)
    measured_hash = checksum(path)
    if measured_hash != track["sha256"]:
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
        measured_hash = inspected.sha256
    if _working_signature(path) != signature:
        raise AppError("FILE_CHANGED", "A working copy changed during analysis verification.")
    return {
        "recording_id": track["recording_id"],
        "path": str(path),
        "sha256": measured_hash,
        "pcm_sha256": track["pcm_sha256"],
        "file_signature": signature,
    }


def _reconcile_analysis(app, manifest):
    return [_reconcile_analysis_track(app, track) for track in manifest["tracks"]]


def _validated_reconciliation(app, tracks, reconciled, *, exact_hash=False):
    """Accept only complete worker results whose checked file identities still match.

    This is an internal finalizer input, never an operator-supplied proof. Workers
    must produce it through full hash/audio checks, not from manifest metadata.
    """
    if not isinstance(reconciled, list) or len(reconciled) != len(tracks):
        raise AppError("RECONCILIATION_STALE", "Analysis verification does not cover every track.")
    by_id = {item.get("recording_id"): item for item in reconciled if isinstance(item, dict)}
    if len(by_id) != len(tracks) or set(by_id) != {t["recording_id"] for t in tracks}:
        raise AppError("RECONCILIATION_STALE", "Analysis verification recording IDs do not match.")
    result = []
    for track in tracks:
        item = by_id[track["recording_id"]]
        path = app.workspace.authorize(track["path"])
        if (
            item.get("path") != str(path)
            or item.get("pcm_sha256") != track["pcm_sha256"]
            or item.get("file_signature") != _working_signature(path)
        ):
            raise AppError(
                "RECONCILIATION_STALE", "A checked working copy changed; repeat verification."
            )
        if exact_hash and item.get("sha256") != track["sha256"]:
            raise AppError(
                "ANALYSIS_CHANGED", "Working copies changed after analysis; record analysis again."
            )
        result.append(
            {
                key: item[key]
                for key in ("recording_id", "path", "sha256", "pcm_sha256", "file_signature")
            }
        )
    return result


def observe_delivery(
    app, delivery_id, observation: DeliveryObservation, *, reconciled=None, job_guard=None
):
    d = _load(app, delivery_id)
    _revision(d, observation.revision)
    if _app_only(d) and observation.stage not in {"imported", "analyzed"}:
        raise AppError(
            "WORKFLOW_SCOPE", "This delivery tracks native app import and analysis only."
        )
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
    if index <= STAGES.index("analyzed"):
        evidence.pop("app_readback", None)
    record = {
        **observation.model_dump(mode="json"),
        "observed_at": timestamp(),
        "evidence_level": "operator_reported",
        "delivery_id": delivery_id,
    }
    if observation.stage == "analyzed" and observation.outcome == "passed":
        try:
            record["assets"] = _validated_reconciliation(
                app,
                manifest["tracks"],
                _reconcile_analysis(app, manifest) if reconciled is None else reconciled,
            )
        except (AppError, OSError) as exc:
            exc = (
                exc
                if isinstance(exc, AppError)
                else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
            )
            evidence[observation.stage] = {**record, "outcome": "failed", "error": exc.as_dict()}
            _save_evidence(app, d, evidence, job_guard=job_guard, job_error=exc.as_dict())
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
            _save_evidence(app, d, evidence, job_guard=job_guard, job_error=exc.as_dict())
            raise exc
    evidence[observation.stage] = record
    _save_evidence(app, d, evidence, job_guard=job_guard)
    return delivery_status(app, delivery_id)


def verify_device(app, delivery_id, revision):
    d = _load(app, delivery_id)
    _revision(d, revision)
    if _app_only(d):
        raise AppError(
            "WORKFLOW_SCOPE", "Use an app-import check for this delivery, not USB readback."
        )
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


def verify_app(app, delivery_id, revision, *, reconciled=None, job_guard=None):
    """Refresh working-file evidence without claiming automated native app readback."""
    d = _load(app, delivery_id)
    _revision(d, revision)
    if not _app_only(d):
        raise AppError("WORKFLOW_SCOPE", "Use device verification for this USB delivery.")
    evidence = dict(d["evidence"])
    if any(evidence.get(stage, {}).get("outcome") != "passed" for stage in STAGES[:2]):
        raise AppError(
            "APP_STAGE_REQUIRED", "Observe native import and analysis before app verification."
        )
    _prepared(app, d)
    assets = evidence["analyzed"]["assets"]
    try:
        if reconciled is None:
            reconciled = []
            for track in assets:
                path = app.workspace.authorize(track["path"])
                signature = _working_signature(path)
                if checksum(path) != track["sha256"]:
                    raise AppError(
                        "ANALYSIS_CHANGED",
                        "Working copies changed after analysis; inspect and record analysis again.",
                    )
                reconciled.append({**track, "file_signature": signature})
        assets = _validated_reconciliation(app, assets, reconciled, exact_hash=True)
        report = {"status": "verified_working_files", "checked_at": timestamp(), "assets": assets}
    except (AppError, OSError) as exc:
        error = (
            exc
            if isinstance(exc, AppError)
            else AppError("FILE_UNAVAILABLE", "A working copy is unavailable.")
        )
        evidence["analyzed"] = {
            **evidence["analyzed"],
            "outcome": "failed",
            "error": error.as_dict(),
        }
        evidence["app_readback"] = {
            "status": "failed",
            "checked_at": timestamp(),
            "error": error.as_dict(),
        }
        _save_evidence(app, d, evidence, job_guard=job_guard, job_error=error.as_dict())
        raise error from None
    evidence["analyzed"] = {**evidence["analyzed"], "assets": assets}
    evidence["app_readback"] = report
    _save_evidence(app, d, evidence, job_guard=job_guard)
    return delivery_status(app, delivery_id, fresh_app_readback=report)


def delivery_status(app, delivery_id, *, fresh_readback=None, fresh_app_readback=None):
    d = _load(app, delivery_id)
    evidence = d["evidence"]
    job = app.job(d["job_id"]) if d["job_id"] else None
    prepared = bool(job and job["state"] == "completed" and job["outcome"] == "complete")
    blockers = []
    if not prepared:
        blockers.append("prepare_working_copies")
    app_only = _app_only(d)
    if not app_only and not evidence.get("device"):
        blockers.append("bind_target_usb")
    for stage in STAGES[:2] if app_only else STAGES:
        if evidence.get(stage, {}).get("outcome") != "passed":
            blockers.append(stage)
    if not app_only and not _readback_passed(evidence):
        blockers.append("device_audio_hash_readback")
    if app_only and evidence.get("app_readback", {}).get("status") != "verified_working_files":
        blockers.append("app_working_file_readback")
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
    pilot_validated = _pilot_passed(app, d)
    return {
        **d,
        "preparation_job": job,
        "prepared_for_import": prepared,
        "current_device": current,
        "native_steps": _steps(d),
        "blockers": blockers,
        "requirements_met_at_last_check": not blockers,
        "app_requirements_met_at_last_check": app_only and not blockers,
        "ready_for_app_use": app_only
        and not blockers
        and fresh_app_readback is not None
        and fresh_app_readback.get("status") == "verified_working_files",
        "ready_for_departure": not app_only
        and not blockers
        and fresh_readback is not None
        and fresh_readback.get("status") == "verified_hashes",
        "readiness_basis": (
            "operator-reported native import/analysis plus working-file verification"
            if app_only
            else "operator-reported native stages and hardware sample plus machine audio hashes"
        ),
        "hardware_verified_automatically": False,
        "native_automation_available": False,
        "last_audio_readback_at": evidence.get("readback", {}).get("checked_at"),
        "last_app_readback_at": evidence.get("app_readback", {}).get("checked_at"),
        "hashes_rechecked_by_status": False,
        "next_step": blockers[0]
        if blockers
        else (
            ("app_import_and_analysis_observed" if fresh_app_readback else "verify_app_before_use")
            if app_only
            else ("eject_safely" if fresh_readback else "verify_device_before_departure")
        ),
        "source_quality": "Source fidelity is unchanged by compatibility conversion.",
        "pilot_required_before_full": False,
        "hardware_required_for_local_preparation": False,
        "preparation_scope": "local_files_only",
        "pilot_validation": (
            "validated"
            if pilot_validated
            else "not_validated"
            if d["request"].get("pilot_delivery_id")
            else "not_supplied"
        ),
        "prepared_without_validated_pilot": prepared
        and d["request"]["phase"] == "full"
        and not pilot_validated,
    }
