"""Synthetic native XML snapshots only; no personal export, app database, or audio reads."""

import hashlib
import os
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import pytest

from djlib.domain.errors import AppError
from djlib.exporting import native_rekordbox
from djlib.exporting.handoff import rekordbox_xml


@pytest.fixture
def manifest():
    return {
        "unique_track_count": 2,
        "tracks": [
            {"recording_id": "rec-one", "path": "/synthetic/prepared/Tone one + mix%.wav"},
            {"recording_id": "rec-two", "path": "/synthetic/prepared/café #2?.flac"},
        ],
        "snapshot": {
            "collections": [
                {
                    "collection_id": "collection-one",
                    "name": "Synthetic demo",
                    "tracks": [{"recording_id": "rec-two"}, {"recording_id": "rec-one"}],
                }
            ]
        },
        "playlists": [
            {
                "collection_id": "collection-one",
                "name": "Synthetic demo",
                "path": "/synthetic/prepared/01 - Synthetic demo.m3u8",
                "track_count": 2,
            }
        ],
    }


def uri(path):
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    return "file://localhost" + quote(path, safe="/:")


def tree(manifest, key_type="0"):
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="7.2.19", Company="AlphaTheta")
    collection = ET.SubElement(root, "COLLECTION", Entries="2")
    for index, track in enumerate(manifest["tracks"], 1):
        ET.SubElement(
            collection,
            "TRACK",
            TrackID=str(index),
            Location=uri(track["path"]),
            AverageBpm="118.50" if index == 1 else "0.00",
            Tonality="Am" if index == 1 else "",
        )
    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(playlists, "NODE", Name="ROOT", Type="0", Count="1")
    playlist = ET.SubElement(
        folder, "NODE", Name="01 - Synthetic demo", Type="1", KeyType=key_type, Entries="2"
    )
    for index in (2, 1):
        key = str(index) if key_type == "0" else uri(manifest["tracks"][index - 1]["path"])
        ET.SubElement(playlist, "TRACK", Key=key)
    return root


def write(tmp_path, root, name="native.xml"):
    path = tmp_path / name
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return path


def codes(report):
    assert all("message" in error for error in report["errors"])
    return {error["code"] for error in report["errors"]}


@pytest.mark.parametrize("key_type", ["0", "1"])
def test_exact_working_paths_native_playlist_order_and_raw_unknown_metadata(
    tmp_path, manifest, monkeypatch, key_type
):
    path = write(tmp_path, tree(manifest, key_type))
    original = path.read_bytes()
    opened = []
    real_open = os.open

    def only_xml_open(value, *args, **kwargs):
        assert Path(value) == path, "Never open music paths or any external reference from XML"
        opened.append(Path(value))
        return real_open(value, *args, **kwargs)

    monkeypatch.setattr(native_rekordbox.os, "open", only_xml_open)
    report = native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert report["status"] == "matched"
    assert report["errors"] == []
    assert report["source"] == {
        "path": str(path),
        "sha256": hashlib.sha256(original).hexdigest(),
        "byte_count": len(original),
    }
    assert report["native_product_version"] == "7.2.19"
    assert report["product"]["name"] == "rekordbox"
    assert opened == [path]
    assert [track["state"] for track in report["tracks"]] == ["matched", "matched"]
    assert report["tracks"][0]["bpm"] == {
        "raw": "118.50",
        "value": 118.5,
        "known": True,
        "verified": False,
    }
    assert report["tracks"][0]["key"]["raw"] == "Am"
    assert report["tracks"][1]["bpm"]["raw"] == "0.00"
    assert report["unknown_bpm_recording_ids"] == ["rec-two"]
    assert report["unknown_key_recording_ids"] == ["rec-two"]
    playlist = report["playlists"][0]
    assert playlist["expected_name"] == "01 - Synthetic demo"
    assert playlist["observed_recording_ids"] == ["rec-two", "rec-one"]
    assert playlist["native_track_ids"] == ["2", "1"]
    assert playlist["membership_match"] is playlist["order_match"] is True
    assert report["scope"] == "native_rekordbox_xml_snapshot"
    assert report["snapshot_only"] is report["manual_accuracy_review_required"] is True
    for field in (
        "ready_for_app_use",
        "ready_for_departure",
        "native_export_verified",
        "hardware_verified",
        "writes_performed",
        "referenced_media_opened",
        "product_identity_authenticated",
    ):
        assert report[field] is False


@pytest.mark.parametrize("key_type", ["0", "1"])
def test_windows_drive_paths_are_decoded_lexically_on_any_host(tmp_path, manifest, key_type):
    manifest["tracks"][0]["path"] = r"C:\DJ Music\Tone + 100%.wav"
    manifest["tracks"][1]["path"] = r"D:\Prepared\café #2?.flac"
    manifest["playlists"][0]["path"] = r"C:\Prepared\01 - Synthetic demo.m3u8"
    path = write(tmp_path, tree(manifest, key_type))
    report = native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert report["status"] == "matched"
    assert report["playlists"][0]["order_match"]
    assert report["tracks"][0]["prepared_path"] == manifest["tracks"][0]["path"]


def test_reordered_native_playlist_does_not_pass_even_with_matching_membership(tmp_path, manifest):
    root = tree(manifest)
    playlist = root.find("PLAYLISTS/NODE/NODE")
    playlist[:] = list(reversed(list(playlist)))
    report = native_rekordbox.inspect_native_rekordbox(write(tmp_path, root), manifest)
    assert report["status"] == "mismatch"
    assert report["playlists"][0]["membership_match"] is True
    assert report["playlists"][0]["order_match"] is False
    assert "NATIVE_PLAYLIST_ORDER_MISMATCH" in codes(report)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("missing-track", "PREPARED_TRACK_MISSING"),
        ("duplicate-id", "DUPLICATE_NATIVE_TRACK_ID"),
        ("duplicate-location", "DUPLICATE_NATIVE_LOCATION"),
        ("missing-id", "NATIVE_TRACK_ID_MISSING"),
        ("missing-reference", "NATIVE_PLAYLIST_REFERENCE_UNRESOLVED"),
        ("duplicate-reference", "DUPLICATE_NATIVE_PLAYLIST_TRACK"),
        ("duplicate-name", "NATIVE_PLAYLIST_AMBIGUOUS"),
        ("wrong-name", "NATIVE_PLAYLIST_MISSING"),
        ("wrong-keytype", "NATIVE_PLAYLIST_KEY_TYPE"),
        ("wrong-count", "NATIVE_ENTRY_COUNT_MISMATCH"),
    ],
)
def test_duplicate_missing_and_ambiguous_native_evidence_never_passes(
    tmp_path, manifest, change, expected
):
    root = tree(manifest)
    collection = root.find("COLLECTION")
    playlist = root.find("PLAYLISTS/NODE/NODE")
    if change == "missing-track":
        collection.remove(collection[0])
        collection.set("Entries", "1")
    elif change == "duplicate-id":
        collection[1].set("TrackID", "1")
    elif change == "duplicate-location":
        collection[1].set("Location", collection[0].get("Location"))
    elif change == "missing-id":
        collection[0].attrib.pop("TrackID")
    elif change == "missing-reference":
        playlist[0].set("Key", "not-a-native-track")
    elif change == "duplicate-reference":
        playlist[0].set("Key", "1")
    elif change == "duplicate-name":
        root.find("PLAYLISTS/NODE").append(deepcopy(playlist))
    elif change == "wrong-name":
        playlist.set("Name", "Synthetic demo")  # The M3U stem's numeric prefix matters.
    elif change == "wrong-keytype":
        playlist.set("KeyType", "2")
    elif change == "wrong-count":
        playlist.set("Entries", "9" * 5000)
    report = native_rekordbox.inspect_native_rekordbox(write(tmp_path, root), manifest)
    assert report["status"] == "mismatch"
    assert expected in codes(report)
    assert report["ready_for_app_use"] is False


@pytest.mark.parametrize(
    "location",
    [
        "https://example.test/tone.wav",
        "file://remote-server/share/tone.wav",
        "file://localhost/synthetic/prepared/../prepared/tone.wav",
        "file:///somewhere/bad%ZZ.wav",
        "file://localhost/synthetic/tone.wav?download=1",
        "file://localhost/synthetic/tone.wav#fragment",
        "file://localhost/%00private/tone.wav",
        "file://localhost//network/share/tone.wav",
    ],
)
def test_nonlocal_or_ambiguous_locations_are_rejected_without_opening_them(
    tmp_path, manifest, location
):
    root = tree(manifest)
    root.find("COLLECTION/TRACK").set("Location", location)
    report = native_rekordbox.inspect_native_rekordbox(write(tmp_path, root), manifest)
    assert report["status"] == "mismatch"
    assert "NATIVE_LOCATION_UNSUPPORTED" in codes(report)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_dtd_and_entity_declarations_are_rejected_before_parse(
    tmp_path, manifest, monkeypatch, encoding
):
    content = '<?xml version="1.0"?><!DOCTYPE DJ_PLAYLISTS [<!ENTITY x SYSTEM "file:///private/secret">]><DJ_PLAYLISTS>&x;</DJ_PLAYLISTS>'
    path = tmp_path / "unsafe.xml"
    path.write_bytes(content.encode(encoding))

    def forbidden_parse(*args, **kwargs):
        pytest.fail("Unsafe declarations must be rejected before parsing")

    monkeypatch.setattr(native_rekordbox.ET, "iterparse", forbidden_parse)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_UNSAFE"


def test_engine_generated_experimental_xml_is_not_native_evidence(tmp_path, manifest):
    for track in manifest["tracks"]:
        track["path"] = str(tmp_path / f"{track['recording_id']}.wav")
    snapshot = {
        "name": "Synthetic demo",
        "tracks": [
            {
                "path": track["path"],
                "artist": "Test Artist",
                "title": track["recording_id"],
                "version": "",
                "properties": {"duration_seconds": 1},
            }
            for track in manifest["tracks"]
        ],
    }
    path = tmp_path / "experimental.xml"
    path.write_text(rekordbox_xml(snapshot), encoding="utf-8")
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_PRODUCT_INVALID"


@pytest.mark.parametrize("kind", ["bytes", "entries", "depth"])
def test_xml_resource_limits_fail_clearly(tmp_path, manifest, monkeypatch, kind):
    path = write(tmp_path, tree(manifest))
    setting = {"bytes": "MAX_XML_BYTES", "entries": "MAX_XML_ENTRIES", "depth": "MAX_XML_DEPTH"}[
        kind
    ]
    monkeypatch.setattr(native_rekordbox, setting, 8 if kind != "depth" else 2)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_LIMIT"


def test_duplicate_manifest_working_paths_are_not_a_valid_comparison(tmp_path, manifest):
    path = write(tmp_path, tree(manifest))
    manifest["tracks"][1]["path"] = manifest["tracks"][0]["path"]
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_MANIFEST_INVALID"


def test_source_modified_during_read_is_not_reported_as_a_stable_snapshot(
    tmp_path, manifest, monkeypatch
):
    path = write(tmp_path, tree(manifest))
    real_stat = Path.stat
    calls = []

    def changed_on_second_stat(self, *args, **kwargs):
        if self == path:
            calls.append(self)
            if len(calls) == 2:
                with self.open("ab") as stream:
                    stream.write(b"\n<!-- changed during inspection -->")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", changed_on_second_stat)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_FILE_CHANGED"


def _symlink_or_skip(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symbolic links is unavailable on this platform/account")


def test_symbolic_xml_source_is_rejected_before_open(tmp_path, manifest, monkeypatch):
    target = write(tmp_path, tree(manifest))
    link = tmp_path / "linked.xml"
    _symlink_or_skip(link, target)

    def forbidden_open(*args, **kwargs):
        pytest.fail("A symbolic XML source must not be opened")

    monkeypatch.setattr(native_rekordbox.os, "open", forbidden_open)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(link, manifest)
    assert error.value.code == "NATIVE_XML_UNSAFE"


@pytest.mark.parametrize("portable_fallback", [False, True])
def test_symlink_swap_at_descriptor_open_never_reads_replacement_bytes(
    tmp_path, manifest, monkeypatch, portable_fallback
):
    path = write(tmp_path, tree(manifest))
    replacement = write(tmp_path, tree(manifest), "replacement.xml")
    probe = tmp_path / "link-probe"
    _symlink_or_skip(probe, replacement)
    probe.unlink()
    real_open, descriptors = os.open, []
    if portable_fallback:
        monkeypatch.delattr(native_rekordbox.os, "O_NOFOLLOW", raising=False)
        monkeypatch.delattr(native_rekordbox.os, "O_NONBLOCK", raising=False)

    def swap_before_open(value, flags, *args, **kwargs):
        assert Path(value) == path
        if not portable_fallback and hasattr(os, "O_NOFOLLOW"):
            assert flags & os.O_NOFOLLOW
        path.unlink()
        path.symlink_to(replacement)
        descriptor = real_open(value, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def forbidden_read(*args, **kwargs):
        pytest.fail("An opened replacement must fail identity checks before any bytes are read")

    monkeypatch.setattr(native_rekordbox.os, "open", swap_before_open)
    monkeypatch.setattr(native_rekordbox.os, "read", forbidden_read)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code in {"NATIVE_XML_UNAVAILABLE", "NATIVE_XML_FILE_CHANGED"}
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not getattr(os, "O_NONBLOCK", 0),
    reason="FIFO swap check requires POSIX FIFO/nonblocking descriptor support",
)
def test_fifo_swapped_in_at_open_is_nonblocking_and_rejected_before_read(
    tmp_path, manifest, monkeypatch
):
    path = write(tmp_path, tree(manifest))
    real_open, descriptors = os.open, []

    def swap_before_open(value, flags, *args, **kwargs):
        # Assert before calling the real open so a regression fails rather than hangs.
        assert flags & os.O_NONBLOCK
        path.unlink()
        os.mkfifo(path)
        descriptor = real_open(value, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def forbidden_read(*args, **kwargs):
        pytest.fail("A FIFO descriptor must fail fstat checks before reading")

    monkeypatch.setattr(native_rekordbox.os, "open", swap_before_open)
    monkeypatch.setattr(native_rekordbox.os, "read", forbidden_read)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == "NATIVE_XML_INVALID"
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_directory_cannot_be_opened_as_native_xml(tmp_path, manifest, monkeypatch):
    def forbidden_open(*args, **kwargs):
        pytest.fail("An existing directory must be rejected before open")

    monkeypatch.setattr(native_rekordbox.os, "open", forbidden_open)
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(tmp_path, manifest)
    assert error.value.code == "NATIVE_XML_INVALID"


def test_keytype_location_cannot_resolve_a_remote_playlist_key(tmp_path, manifest):
    root = tree(manifest, "1")
    root.find("PLAYLISTS/NODE/NODE/TRACK").set("Key", "https://example.test/audio.mp3")
    report = native_rekordbox.inspect_native_rekordbox(write(tmp_path, root), manifest)
    assert report["status"] == "mismatch"
    assert "NATIVE_PLAYLIST_REFERENCE_UNRESOLVED" in codes(report)
    assert report["playlists"][0]["order_match"] is False


@pytest.mark.parametrize("change", ["malformed", "missing-version", "wrong-root"])
def test_malformed_or_unattributed_xml_cannot_be_native_evidence(tmp_path, manifest, change):
    root = tree(manifest)
    if change == "missing-version":
        root.find("PRODUCT").attrib.pop("Version")
    elif change == "wrong-root":
        root.tag = "OTHER_PLAYLISTS"
    path = write(tmp_path, root)
    if change == "malformed":
        path.write_text("<DJ_PLAYLISTS>", encoding="utf-8")
    with pytest.raises(AppError) as error:
        native_rekordbox.inspect_native_rekordbox(path, manifest)
    assert error.value.code == (
        "NATIVE_XML_PRODUCT_INVALID" if change == "missing-version" else "NATIVE_XML_INVALID"
    )


def test_invalid_and_nonfinite_metadata_stays_raw_and_unknown(tmp_path, manifest):
    root = tree(manifest)
    root.find("COLLECTION/TRACK").set("AverageBpm", "nan")
    root.find("COLLECTION/TRACK").set("Tonality", "guess")
    report = native_rekordbox.inspect_native_rekordbox(write(tmp_path, root), manifest)
    assert report["status"] == "matched"  # Membership is independent from analysis claims.
    assert report["tracks"][0]["bpm"] == {
        "raw": "nan",
        "value": None,
        "known": False,
        "verified": False,
    }
    assert report["tracks"][0]["key"]["raw"] == "guess"
    assert report["unknown_key_recording_ids"] == ["rec-one", "rec-two"]
    assert report["manual_accuracy_review_required"] is True
