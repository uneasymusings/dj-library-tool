"""Read a native rekordbox XML snapshot without opening its referenced music paths.

The documented XML keys are TrackID (KeyType 0) or file Location (KeyType 1):
https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf
Product labels and XML metadata are assertions in a snapshot, not native app,
analysis-accuracy, USB-export, or physical-player verification.
"""

import hashlib
import io
import math
import ntpath
import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from djlib.audio.file_identity import descriptor_snapshot, path_snapshot
from djlib.domain.errors import AppError

MAX_XML_BYTES = 32 * 1024 * 1024
MAX_XML_ENTRIES = 100_000
MAX_XML_DEPTH = 64
MAX_REPORT_ERRORS = 200


class _Errors:
    def __init__(self):
        self.items = []
        self.total = 0

    def add(self, code, message, **context):
        self.total += 1
        if len(self.items) < MAX_REPORT_ERRORS:
            self.items.append({"code": code, "message": message, **context})


def _read_xml(path):
    descriptor = None
    try:
        before = path_snapshot(path)
        if before.is_reparse:
            raise AppError("NATIVE_XML_UNSAFE", "Choose the XML file itself, not a symbolic link.")
        if not before.is_regular:
            raise AppError("NATIVE_XML_INVALID", "Choose a regular native XML export file.")
        if before.size > MAX_XML_BYTES:
            raise AppError("NATIVE_XML_LIMIT", "Native XML is limited to 32 MiB.")
        # NOFOLLOW rejects a link swapped in after lstat. NONBLOCK prevents a
        # substituted POSIX FIFO from hanging before fstat can reject it.
        # Windows handle identities reject a changed target before reading bytes.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        opened = descriptor_snapshot(descriptor)
        if not opened.is_regular or opened.is_reparse:
            raise AppError("NATIVE_XML_INVALID", "The opened XML descriptor is not a regular file.")
        if opened.signature != before.signature:
            raise AppError("NATIVE_XML_FILE_CHANGED", "The XML path changed before it was opened.")
        data = bytearray()
        while len(data) <= MAX_XML_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_XML_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = descriptor_snapshot(descriptor)
        current = path_snapshot(path)
    except OSError as exc:
        raise AppError("NATIVE_XML_UNAVAILABLE", "The native XML file is unavailable.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > MAX_XML_BYTES:
        raise AppError("NATIVE_XML_LIMIT", "Native XML is limited to 32 MiB.")
    if (
        not current.is_regular
        or current.is_reparse
        or before.signature != after.signature
        or after.signature != current.signature
        or len(data) != after.size
    ):
        raise AppError("NATIVE_XML_FILE_CHANGED", "The native XML changed while being read.")
    data = bytes(data)
    # Removing NULs also catches UTF-16/32 declarations before any XML parser runs.
    # Predefined XML escapes such as &amp; remain allowed; entity declarations do not.
    if re.search(rb"<!\s*(?:doctype|entity)\b", data.replace(b"\x00", b""), re.IGNORECASE):
        raise AppError("NATIVE_XML_UNSAFE", "DTD and entity declarations are not accepted.")
    try:
        parser = ET.iterparse(io.BytesIO(data), events=("start", "end"))
        count = depth = 0
        for event, _ in parser:
            if event == "start":
                count += 1
                depth += 1
                if count > MAX_XML_ENTRIES or depth > MAX_XML_DEPTH:
                    raise AppError(
                        "NATIVE_XML_LIMIT", "Native XML exceeds its element/depth bound."
                    )
            else:
                depth -= 1
        root = parser.root
    except (ET.ParseError, ValueError) as exc:
        raise AppError("NATIVE_XML_INVALID", "The native export is not valid XML.") from exc
    return (
        root,
        {
            "path": str(path.absolute()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_count": len(data),
        },
        count,
    )


def _path_key(value):
    """Lexical local paths only: no filesystem lookup, symlink resolution or drive access."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("Path must be a bounded nonempty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Control characters in path")
    if re.match(r"^[A-Za-z]:[/\\]", value):
        path = value.replace("\\", "/")
        if any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("Dot segments in path")
        normalized = ntpath.normpath(path).replace("\\", "/")
        return "windows", normalized[0].upper() + normalized[1:]
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("Not an absolute local path")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Dot segments in path")
    return "posix", posixpath.normpath(value)


def _location(value):
    if not isinstance(value, str) or len(value) > 16_384:
        raise ValueError("Invalid location")
    parts = urlsplit(value)
    if (
        parts.scheme != "file"
        or parts.netloc.casefold() not in {"", "localhost"}
        or parts.query
        or parts.fragment
        or re.search(r"%(?![0-9A-Fa-f]{2})", parts.path)
    ):
        raise ValueError("Only local file URIs are accepted")
    decoded = unquote(parts.path, encoding="utf-8", errors="strict")
    if re.match(r"^/[A-Za-z]:/", decoded):
        decoded = decoded[1:]
    return _path_key(decoded)


def _manifest(manifest):
    try:
        tracks, playlists = manifest["tracks"], manifest["playlists"]
        collections = manifest["snapshot"]["collections"]
        if not isinstance(tracks, list) or not tracks or len(tracks) > MAX_XML_ENTRIES:
            raise ValueError
        if not isinstance(playlists, list) or not playlists or len(playlists) > MAX_XML_ENTRIES:
            raise ValueError
        by_id, by_path = {}, {}
        for track in tracks:
            identifier, path = track["recording_id"], _path_key(track["path"])
            if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                raise ValueError
            if identifier in by_id or path in by_path:
                raise ValueError
            by_id[identifier], by_path[path] = track, identifier
        collection_by_id = {collection["collection_id"]: collection for collection in collections}
        if len(collection_by_id) != len(collections):
            raise ValueError
        expected, names, collection_ids = [], set(), set()
        for playlist in playlists:
            collection_id = playlist["collection_id"]
            path = playlist["path"]
            _path_key(path)
            filename = path.replace("\\", "/").rsplit("/", 1)[-1]
            name, extension = posixpath.splitext(filename)
            if not name or extension.casefold() not in {".m3u", ".m3u8"}:
                raise ValueError
            ids = [track["recording_id"] for track in collection_by_id[collection_id]["tracks"]]
            if (
                not ids
                or len(ids) != len(set(ids))
                or not set(ids) <= by_id.keys()
                or name in names
                or collection_id in collection_ids
                or playlist.get("track_count", len(ids)) != len(ids)
            ):
                raise ValueError
            names.add(name)
            collection_ids.add(collection_id)
            expected.append({"collection_id": collection_id, "name": name, "ids": ids})
        if manifest.get("unique_track_count", len(tracks)) != len(tracks):
            raise ValueError
        return by_id, by_path, expected
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AppError(
            "NATIVE_XML_MANIFEST_INVALID", "Supply an unambiguous prepared delivery manifest."
        ) from exc


def _metadata(track):
    raw_bpm, raw_key = track.get("AverageBpm"), track.get("Tonality")
    bpm = None
    try:
        parsed = float(raw_bpm)
        if math.isfinite(parsed) and parsed > 0:
            bpm = parsed
    except (TypeError, ValueError, OverflowError):
        pass
    key = None
    if raw_key and re.fullmatch(
        r"(?:[1-9]|1[0-2])[ABdm]|[A-Ga-g][#b]?(?:m|maj|min|major|minor)?", raw_key.strip()
    ):
        key = raw_key.strip()
    return {
        "bpm": {"raw": raw_bpm, "value": bpm, "known": bpm is not None, "verified": False},
        "key": {"raw": raw_key, "value": key, "known": key is not None, "verified": False},
        "metadata_source": "native_xml_attributes",
        "analysis_accuracy_verified": False,
    }


def _entry_count(node, actual, errors, context):
    declared = node.get("Entries")
    if declared is not None and (
        len(declared) > 9
        or not declared.isascii()
        or not declared.isdecimal()
        or int(declared) != actual
    ):
        errors.add(
            "NATIVE_ENTRY_COUNT_MISMATCH",
            "Declared XML count differs from its entries.",
            context=context,
        )


def inspect_native_rekordbox(xml_path: Path, manifest: dict) -> dict:
    """Compare native XML collection/playlist evidence against prepared working copies only."""
    expected_tracks, expected_paths, expected_playlists = _manifest(manifest)
    root, source, element_count = _read_xml(Path(xml_path))
    products, collections, playlist_roots = (
        root.findall("PRODUCT"),
        root.findall("COLLECTION"),
        root.findall("PLAYLISTS"),
    )
    if root.tag != "DJ_PLAYLISTS" or len(collections) != 1 or len(playlist_roots) != 1:
        raise AppError("NATIVE_XML_INVALID", "Expected one rekordbox collection and playlist tree.")
    if (
        len(products) != 1
        or products[0].get("Name", "").casefold() != "rekordbox"
        or not products[0].get("Version", "").strip()
    ):
        raise AppError(
            "NATIVE_XML_PRODUCT_INVALID",
            "Use a native rekordbox XML export with its product version.",
        )
    product = {
        "name": products[0].get("Name"),
        "version": products[0].get("Version"),
        "company": products[0].get("Company"),
    }
    errors, native_ids, native_paths = _Errors(), defaultdict(list), defaultdict(list)
    collection_tracks = collections[0].findall("TRACK")
    _entry_count(collections[0], len(collection_tracks), errors, "collection")
    for index, node in enumerate(collection_tracks):
        identifier = node.get("TrackID")
        entry = {"track_id": identifier, "node": node, "path": None}
        if not identifier:
            errors.add("NATIVE_TRACK_ID_MISSING", "A collection track has no TrackID.", entry=index)
        else:
            native_ids[identifier].append(entry)
        try:
            entry["path"] = _location(node.get("Location"))
            native_paths[entry["path"]].append(entry)
        except (ValueError, UnicodeError):
            errors.add(
                "NATIVE_LOCATION_UNSUPPORTED",
                "A track does not reference a valid local file URI.",
                track_id=identifier,
            )
    for identifier, entries in native_ids.items():
        if len(entries) > 1:
            errors.add(
                "DUPLICATE_NATIVE_TRACK_ID",
                "TrackID is not unique in the native collection.",
                track_id=identifier,
                count=len(entries),
            )
    for path, entries in native_paths.items():
        if len(entries) > 1:
            errors.add(
                "DUPLICATE_NATIVE_LOCATION",
                "Several native tracks reference one local path.",
                path=path[1],
                count=len(entries),
            )
    tracks = []
    for identifier, expected in expected_tracks.items():
        entries = native_paths.get(_path_key(expected["path"]), [])
        valid = (
            len(entries) == 1
            and entries[0]["track_id"]
            and len(native_ids[entries[0]["track_id"]]) == 1
        )
        state = "matched" if valid else "missing" if not entries else "ambiguous"
        if not valid:
            errors.add(
                "PREPARED_TRACK_MISSING" if not entries else "PREPARED_TRACK_AMBIGUOUS",
                "The prepared path has no single native track identity.",
                recording_id=identifier,
            )
        tracks.append(
            {
                "recording_id": identifier,
                "prepared_path": expected["path"],
                "state": state,
                "native_track_ids": [entry["track_id"] for entry in entries],
                **_metadata(entries[0]["node"] if valid else {}),
            }
        )
    native_playlists = defaultdict(list)
    for node in playlist_roots[0].iter("NODE"):
        if node.get("Type") == "1":
            native_playlists[node.get("Name", "")].append(node)
    playlists = []
    for expected in expected_playlists:
        candidates = native_playlists.get(expected["name"], [])
        report = {
            "collection_id": expected["collection_id"],
            "expected_name": expected["name"],
            "candidate_count": len(candidates),
            "expected_recording_ids": expected["ids"],
            "observed_recording_ids": [],
            "native_track_ids": [],
            "membership_match": False,
            "order_match": False,
            "state": "mismatch",
        }
        playlists.append(report)
        if len(candidates) != 1:
            errors.add(
                "NATIVE_PLAYLIST_MISSING" if not candidates else "NATIVE_PLAYLIST_AMBIGUOUS",
                "Expected playlist name must identify exactly one native playlist.",
                playlist=expected["name"],
                count=len(candidates),
            )
            continue
        node = candidates[0]
        key_type = node.get("KeyType")
        report["key_type"] = key_type
        references = node.findall("TRACK")
        _entry_count(node, len(references), errors, expected["name"])
        if key_type not in {"0", "1"}:
            errors.add(
                "NATIVE_PLAYLIST_KEY_TYPE",
                "Native playlist KeyType must be TrackID 0 or Location 1.",
                playlist=expected["name"],
            )
            continue
        observed_paths, unresolved = [], False
        for reference in references:
            key = reference.get("Key")
            if key_type == "0":
                found = native_ids.get(key, [])
            else:
                try:
                    found = native_paths.get(_location(key), [])
                except (ValueError, UnicodeError):
                    found = []
            if (
                len(found) != 1
                or found[0]["path"] is None
                or not found[0]["track_id"]
                or len(native_ids[found[0]["track_id"]]) != 1
                or len(native_paths[found[0]["path"]]) != 1
            ):
                errors.add(
                    "NATIVE_PLAYLIST_REFERENCE_UNRESOLVED",
                    "A playlist key is missing or ambiguous in the native collection.",
                    playlist=expected["name"],
                    key=key,
                )
                unresolved = True
                observed_paths.append(None)
                report["native_track_ids"].append(None)
                report["observed_recording_ids"].append(None)
            else:
                entry = found[0]
                observed_paths.append(entry["path"])
                report["native_track_ids"].append(entry["track_id"])
                report["observed_recording_ids"].append(expected_paths.get(entry["path"]))
        if len(observed_paths) != len(set(observed_paths)):
            errors.add(
                "DUPLICATE_NATIVE_PLAYLIST_TRACK",
                "A native playlist repeats a track reference.",
                playlist=expected["name"],
            )
            unresolved = True
        wanted_paths = [
            _path_key(expected_tracks[identifier]["path"]) for identifier in expected["ids"]
        ]
        report["membership_match"] = not unresolved and Counter(observed_paths) == Counter(
            wanted_paths
        )
        report["order_match"] = not unresolved and observed_paths == wanted_paths
        report["state"] = "matched" if report["order_match"] else "mismatch"
        if not report["membership_match"]:
            errors.add(
                "NATIVE_PLAYLIST_MEMBERSHIP_MISMATCH",
                "Native playlist members differ from the prepared manifest.",
                playlist=expected["name"],
            )
        elif not report["order_match"]:
            errors.add(
                "NATIVE_PLAYLIST_ORDER_MISMATCH",
                "Native playlist order differs from the prepared manifest.",
                playlist=expected["name"],
            )
    return {
        "schema_version": "1",
        "status": "mismatch" if errors.total else "matched",
        "source": source,
        "product": product,
        "native_product_version": product["version"],
        "xml_element_count": element_count,
        "native_collection_track_count": len(collection_tracks),
        "tracks": tracks,
        "playlists": playlists,
        "errors": errors.items,
        "error_count": errors.total,
        "errors_truncated": errors.total > len(errors.items),
        "unknown_bpm_recording_ids": [
            track["recording_id"] for track in tracks if not track["bpm"]["known"]
        ],
        "unknown_key_recording_ids": [
            track["recording_id"] for track in tracks if not track["key"]["known"]
        ],
        "scope": "native_rekordbox_xml_snapshot",
        "snapshot_only": True,
        "manual_accuracy_review_required": True,
        "product_identity_authenticated": False,
        "ready_for_app_use": False,
        "ready_for_departure": False,
        "native_export_verified": False,
        "hardware_verified": False,
        "writes_performed": False,
        "referenced_media_opened": False,
    }
