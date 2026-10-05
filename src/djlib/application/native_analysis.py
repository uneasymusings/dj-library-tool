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
