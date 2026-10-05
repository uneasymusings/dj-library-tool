"""Bring rekordbox's own BPM/key analysis back into catalog annotations.

rekordbox analyzes audio; djlib does not. A native Collection XML export lists each track's
file Location with AverageBpm and Tonality. Tracks are matched by exact local path, either an
original catalog location or a prepared delivery working copy, never by labels. Values keep
``source: "rekordbox_analysis"`` and ``verified: false``; operator and tag-sourced values that
someone chose explicitly are never overwritten.
"""

import unicodedata
from collections import defaultdict

from sqlalchemy import select, update

from djlib.application.service import require
from djlib.domain.errors import AppError
from djlib.exporting.native_rekordbox import _location, _metadata, _path_key, _read_xml
from djlib.persistence.models import Asset, AssetRevision, FileLocation, Job, JobItem, timestamp
from djlib.persistence.organization_models import RecordingAnnotation

SOURCE = "rekordbox_analysis"
MAX_EXAMPLES = 10


def _key(path_key):
    system, path = path_key
    # macOS may store decomposed accents where rekordbox writes composed ones.
    return system, unicodedata.normalize("NFC", path)


def _catalog_paths(session) -> dict:
    """Exact local paths that identify one catalog revision: originals and working copies."""
    index = defaultdict(set)
    rows = session.execute(
        select(FileLocation.path, AssetRevision.id, Asset.recording_id)
        .join(AssetRevision, FileLocation.revision_id == AssetRevision.id)
        .join(Asset, AssetRevision.asset_id == Asset.id)
    )
    for path, revision_id, recording_id in rows:
        try:
            index[_key(_path_key(path))].add((recording_id, revision_id))
        except ValueError:
            continue
    items = session.scalars(
        select(JobItem)
        .join(Job, JobItem.job_id == Job.id)
        .where(Job.kind == "delivery", JobItem.state == "succeeded")
    )
    for item in items:
        result = item.result or {}
        try:
            index[_key(_path_key(result["path"]))].add(
                (result["recording_id"], result["asset_revision_id"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return index


def import_rekordbox_analysis(app, path: str) -> dict:
    xml_path = app.workspace.authorize(path)
    root, source, _ = _read_xml(xml_path)
    products, collections = root.findall("PRODUCT"), root.findall("COLLECTION")
    if (
        root.tag != "DJ_PLAYLISTS"
        or len(collections) != 1
        or len(products) != 1
        or products[0].get("Name", "").casefold() != "rekordbox"
    ):
        raise AppError(
            "NATIVE_XML_INVALID",
            "Use rekordbox's File > Export Collection in xml format.",
        )
    app_version = products[0].get("Version") or None
    counts = {
        "tracks_in_xml": 0,
        "matched": 0,
        "updated": 0,
        "unchanged": 0,
        "kept_your_values": 0,
        "not_analyzed": 0,
        "unmatched": 0,
        "ambiguous": 0,
    }
    unmatched: list[str] = []
    now = timestamp()
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        index = _catalog_paths(session)
        updates: dict[tuple[str, str], dict] = {}
        for node in collections[0].findall("TRACK"):
            counts["tracks_in_xml"] += 1
            try:
                targets = index.get(_key(_location(node.get("Location"))), set())
            except (ValueError, UnicodeError):
                targets = set()
            if not targets:
                counts["unmatched"] += 1
                if len(unmatched) < MAX_EXAMPLES:
                    unmatched.append(
                        " - ".join(filter(None, (node.get("Artist"), node.get("Name"))))
                    )
                continue
            if len({recording for recording, _ in targets}) > 1:
                counts["ambiguous"] += 1
                continue
            counts["matched"] += 1
            metadata = _metadata(node)
            found = {
                field: {"value": metadata[field]["value"], "source": SOURCE, "verified": False}
                for field in ("bpm", "key")
                if metadata[field]["known"]
            }
            if not found:
                counts["not_analyzed"] += 1
                continue
            for target in targets:
                updates.setdefault(target, {}).update(found)
        for (recording_id, revision_id), found in updates.items():
            row = session.get(RecordingAnnotation, revision_id)
            annotations = dict(row.annotations) if row else {}
            changed = False
            for field, value in found.items():
                current = annotations.get(field)
                if current and current.get("source") != SOURCE:
                    counts["kept_your_values"] += 1
                elif current != value:
                    annotations[field] = value
                    changed = True
            if not changed:
                counts["unchanged"] += 1
                continue
            counts["updated"] += 1
            if row:
                session.execute(
                    update(RecordingAnnotation)
                    .where(
                        RecordingAnnotation.asset_revision_id == revision_id,
                        RecordingAnnotation.revision == row.revision,
                    )
                    .values(annotations=annotations, revision=row.revision + 1, updated_at=now)
                )
            else:
                require(session, AssetRevision, revision_id)
                session.add(
                    RecordingAnnotation(
                        asset_revision_id=revision_id,
                        recording_id=recording_id,
                        revision=1,
                        annotations=annotations,
                        created_at=now,
                        updated_at=now,
                    )
                )
    return {
        "xml": source,
        "app": "rekordbox",
        "app_version": app_version,
        **counts,
        "unmatched_examples": unmatched,
        "source": SOURCE,
        "analysis_accuracy_verified": False,
        "matched_by": "exact_file_location",
        "source_modified": False,
    }


def _catalog_names(session) -> dict:
    """File name → catalog revisions, for originals and prepared working copies."""
    from pathlib import PurePath

    index = defaultdict(set)
    for path_key, targets in _catalog_paths(session).items():
        name = unicodedata.normalize("NFC", PurePath(path_key[1]).name)
        index[name] |= targets
    return index


def sync_rekordbox_analysis(app, root=None) -> dict:
    """Read rekordbox's analysis files in the background: BPM and cues, no UI, no database.

    Incremental: only files whose size or modification time changed are parsed again.
    Tracks match by file name when exactly one catalog recording has that name.
    """
    import json

    from djlib.exporting.rekordbox_anlz import analysis_files, default_root, parse
    from djlib.workspace import atomic_json

    root = root or default_root()
    if root is None:
        raise AppError("NATIVE_ANALYSIS_UNAVAILABLE", "rekordbox's analysis folder was not found.")
    cache_path = app.workspace.runtime / "rekordbox-anlz.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    files, parsed, changed = {}, [], 0
    for path in analysis_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = str(path.relative_to(root))
        signature = [stat.st_mtime_ns, stat.st_size]
        entry = cache.get("files", {}).get(relative)
        if not entry or entry[:2] != signature:
            changed += 1
            entry = [*signature, parse(path)]
        files[relative] = entry
        if entry[2]:
            parsed.append((entry[0], entry[2]))
    # rekordbox can leave several analysis folders for one file name; the newest wins.
    newest: dict[str, tuple[int, dict]] = {}
    for mtime, item in parsed:
        if item["name"] not in newest or mtime > newest[item["name"]][0]:
            newest[item["name"]] = (mtime, item)
    parsed = [item for _, item in newest.values()]
    counts = {
        "analysis_files": len(files),
        "changed_files": changed,
        "with_beat_grid": sum(1 for item in parsed if item["bpm"]),
        "matched": 0,
        "updated": 0,
        "unchanged": 0,
        "kept_your_values": 0,
        "ambiguous": 0,
        "unmatched": 0,
    }
    now = timestamp()
    with app.db.transaction() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        index = _catalog_names(session)
        for item in parsed:
            targets = index.get(item["name"], set())
            if not targets:
                counts["unmatched"] += 1
                continue
            if len({recording for recording, _ in targets}) > 1:
                counts["ambiguous"] += 1
                continue
            counts["matched"] += 1
            native = {
                "beats": item["beats"],
                "hot_cues": item["hot_cues"],
                "memory_cues": item["memory_cues"],
            }
            for recording_id, revision_id in targets:
                row = session.get(RecordingAnnotation, revision_id)
                annotations = dict(row.annotations) if row else {}
                before = dict(annotations)
                current = annotations.get("bpm")
                if item["bpm"]:
                    value = {"value": item["bpm"], "source": SOURCE, "verified": False}
                    if current and current.get("source") != SOURCE:
                        counts["kept_your_values"] += 1
                    else:
                        annotations["bpm"] = value
                annotations["rekordbox"] = native
                if annotations == before:
                    counts["unchanged"] += 1
                    continue
                counts["updated"] += 1
                if row:
                    session.execute(
                        update(RecordingAnnotation)
                        .where(
                            RecordingAnnotation.asset_revision_id == revision_id,
                            RecordingAnnotation.revision == row.revision,
                        )
                        .values(annotations=annotations, revision=row.revision + 1, updated_at=now)
                    )
                else:
                    session.add(
                        RecordingAnnotation(
                            asset_revision_id=revision_id,
                            recording_id=recording_id,
                            revision=1,
                            annotations=annotations,
                            created_at=now,
                            updated_at=now,
                        )
                    )
    atomic_json(cache_path, {"root": str(root), "files": files, "synced_at": now})
    return {
        **counts,
        "source": SOURCE,
        "read_from": "rekordbox_analysis_files",
        "matched_by": "unique_file_name",
        "key_included": False,
        "analysis_accuracy_verified": False,
        "source_modified": False,
    }
