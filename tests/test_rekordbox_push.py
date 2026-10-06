"""rekordbox automation against a simulated UI, plus its analysis files and XML format."""

import functools
import json
import struct
import time
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from djlib.application.native_analysis import sync_rekordbox_analysis
from djlib.domain.errors import AppError
from djlib.exporting import usb_check
from djlib.exporting.native_rekordbox import playlist_report
from djlib.exporting.rekordbox_anlz import parse
from djlib.interfaces import service
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.rekordbox_cli import playlist_file_name
from djlib.native import rekordbox_mac
from tests import pdb_fixture
from tests.test_headline_workflow import build


def write_export(path: Path, playlist: str, tracks: list[tuple[str, str]], key_type="0") -> Path:
    collection = "".join(
        f"<TRACK TrackID='{n}' Name='t{n}' Location={quoteattr(Path(p).as_uri())}"
        f" AverageBpm={quoteattr(bpm)} Tonality='8A'/>"
        for n, (p, bpm) in enumerate(tracks, 1)
    )
    members = "".join(
        f"<TRACK Key={quoteattr(str(n) if key_type == '0' else Path(p).as_uri())}/>"
        for n, (p, _) in enumerate(tracks, 1)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?><DJ_PLAYLISTS Version='1.0.0'>"
        "<PRODUCT Name='rekordbox' Version='7.2.19' Company='AlphaTheta'/>"
        f"<COLLECTION Entries='{len(tracks)}'>{collection}</COLLECTION>"
        "<PLAYLISTS><NODE Type='0' Name='ROOT' Count='1'>"
        f"<NODE Name={quoteattr(playlist)} Type='1' KeyType='{key_type}' Entries='{len(tracks)}'>"
        f"{members}</NODE></NODE></PLAYLISTS></DJ_PLAYLISTS>",
        encoding="utf-8",
    )
    return path


def write_anlz(folder: Path, file_name: str, bpm: float, hot_cues: int = 2) -> Path:
    """A minimal ANLZ0000.DAT: header, PPTH (file name), PQTZ (beat grid), PCOB (hot cues)."""

    def tag(fourcc: bytes, header: bytes, body: bytes, header_len: int) -> bytes:
        return fourcc + struct.pack(">II", header_len, 12 + len(header) + len(body)) + header + body

    path = f"?/{file_name}\x00".encode("utf-16-be")
    ppth = tag(b"PPTH", struct.pack(">I", len(path)), path, 16)
    beats = b"".join(struct.pack(">HHI", n % 4 + 1, round(bpm * 100), n * 500) for n in range(8))
    pqtz = tag(b"PQTZ", struct.pack(">III", 0, 0x80000, 8), beats, 24)
    pcob = tag(b"PCOB", struct.pack(">IHH", 1, 0, hot_cues) + b"\x00" * 4, b"", 24)
    tags = ppth + pqtz + pcob
    data = b"PMAI" + struct.pack(">II", 28, 28 + len(tags)) + b"\x00" * 16 + tags
    target = folder / "ANLZ0000.DAT"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


class FakeRekordbox:
    def __init__(self, monkeypatch):
        self.imported: list[Path] = []
        self.present: set[str] = set()
        monkeypatch.setattr(rekordbox_mac, "ensure_supported", lambda: None)
        monkeypatch.setattr(rekordbox_mac, "import_playlist", self.import_playlist)
        monkeypatch.setattr(rekordbox_mac, "playlists", lambda: set(self.present))
        monkeypatch.setattr(rekordbox_mac, "export_collection", self.export_collection)

    def import_playlist(self, playlist: Path):
        self.imported.append(playlist)
        self.present.add(playlist.stem)

    def export_collection(self, destination: Path) -> Path:
        if not self.imported:
            return write_export(destination, "Existing playlist", [])
        lines = self.imported[-1].read_text(encoding="utf-8").splitlines()
        paths = [line for line in lines if line and not line.startswith("#")]
        return write_export(destination, self.imported[-1].stem, [(p, "126.00") for p in paths])


def test_playlist_report_reads_both_key_types(tmp_path):
    tracks = [(str(tmp_path / "a.mp3"), "124.00"), (str(tmp_path / "b.mp3"), "0.00")]
    for key_type in ("0", "1"):
        xml = write_export(tmp_path / f"x{key_type}.xml", "Set 0", tracks, key_type)
        report = playlist_report(xml, "Set 0", [p for p, _ in tracks] + [str(tmp_path / "c.mp3")])
        assert report["playlist_found"] and report["entries"] == 2
        assert (report["expected"], report["matched"], report["analyzed"]) == (3, 2, 1)
        assert report["missing_paths"] == [str(tmp_path / "c.mp3")]
    assert playlist_report(xml, "Other", [])["playlist_found"] is False


def test_push_imports_quickly_and_verifies_on_request(
    application, library_http, audio_factory, monkeypatch
):
    sources = [audio_factory(f"push-{n}.wav", frequency=300 + 40 * n) for n in range(2)]
    collection_id = build(
        library_http, sources, [("Velvet Static", f"Push {n}") for n in range(2)]
    )["result"]["collection_id"]
    fake = FakeRekordbox(monkeypatch)
    workspace = ["--workspace", str(application.workspace.root)]

    reply = CliRunner().invoke(cli_app, [*workspace, "rekordbox", "push", collection_id])
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert fake.imported[0].name == "Owned.m3u8"
    crate = result["crates"][0]
    assert crate["status"] == "imported" and crate["playlist_found"] is True
    assert (
        result["verified_by"] == "rekordbox_menu" and result["database_modified_directly"] is False
    )

    again = CliRunner().invoke(
        cli_app, [*workspace, "rekordbox", "push", collection_id, "--verify"]
    )
    assert again.exit_code == 0, again.output
    verified = json.loads(again.stdout)["result"]
    assert len(fake.imported) == 1  # an existing playlist is never imported twice
    crate = verified["crates"][0]
    assert crate["status"] == "already_in_rekordbox"
    assert crate["matched"] == crate["expected"] == crate["analyzed"] == 2
    rows = library_http.get("/library").json()["result"]["tracks"]
    assert {row["dj"]["bpm"] for row in rows if row["title"].startswith("Push")} == {126.0}


def test_push_reports_a_playlist_rekordbox_did_not_create(
    application, library_http, audio_factory, monkeypatch
):
    collection_id = build(
        library_http, [audio_factory("lost.wav", frequency=480)], [("Velvet Static", "Lost")]
    )["result"]["collection_id"]
    fake = FakeRekordbox(monkeypatch)
    monkeypatch.setattr(rekordbox_mac, "import_playlist", lambda playlist: None)
    reply = CliRunner().invoke(
        cli_app,
        ["--workspace", str(application.workspace.root), "rekordbox", "push", collection_id],
    )
    assert reply.exit_code == 2
    assert json.loads(reply.stdout)["error"]["code"] == "APP_IMPORT_NOT_FOUND"
    assert fake.present == set()


def test_push_explains_the_macos_permission(application, monkeypatch):
    def refuse():
        raise AppError("APP_AUTOMATION_NOT_ALLOWED", "Allow your terminal app.")

    monkeypatch.setattr(rekordbox_mac, "ensure_supported", refuse)
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "rekordbox", "push", "x"]
    )
    assert reply.exit_code == 2
    assert json.loads(reply.stdout)["error"]["code"] == "APP_AUTOMATION_NOT_ALLOWED"


def test_analysis_files_parse_bpm_cues_and_name(tmp_path):
    parsed = parse(write_anlz(tmp_path / "a" / "b", "Velvet Static - Night Bus.flac", 124.5, 3))
    assert parsed == {
        "name": "Velvet Static - Night Bus.flac",
        "bpm": 124.5,
        "beats": 8,
        "hot_cues": 3,
        "memory_cues": 0,
    }
    junk = tmp_path / "junk" / "ANLZ0000.DAT"
    junk.parent.mkdir()
    junk.write_bytes(b"PMAI\x00\x00")
    assert parse(junk) is None


def test_background_sync_matches_unique_names_and_is_incremental(
    application, library_http, audio_factory, tmp_path
):
    sources = [audio_factory(f"sync-{n}.wav", frequency=500 + 30 * n) for n in range(2)]
    build(library_http, sources, [("Velvet Static", f"Sync {n}") for n in range(2)])
    analysis = tmp_path / "USBANLZ"
    write_anlz(analysis / "001" / "x", sources[0].name, 121.0)
    write_anlz(analysis / "002" / "y", "not-in-catalog.mp3", 99.0)
    first = sync_rekordbox_analysis(application, analysis)
    assert (first["analysis_files"], first["changed_files"], first["matched"]) == (2, 2, 1)
    assert first["updated"] == 1 and first["unmatched"] == 1 and first["key_included"] is False
    second = sync_rekordbox_analysis(application, analysis)
    assert (second["changed_files"], second["updated"], second["unchanged"]) == (0, 0, 1)
    row = next(
        t for t in library_http.get("/library").json()["result"]["tracks"] if t["title"] == "Sync 0"
    )
    assert row["dj"]["bpm"] == 121.0 and row["dj"]["bpm_source"] == "rekordbox_analysis"


def test_route_without_a_path_reads_analysis_files(library_http, tmp_path, monkeypatch):
    monkeypatch.setenv("DJLIB_REKORDBOX_ANLZ", str(tmp_path / "USBANLZ"))
    (tmp_path / "USBANLZ").mkdir()
    reply = library_http.post("/analysis/rekordbox", json={"path": None}).json()
    assert reply["ok"] and reply["result"]["read_from"] == "rekordbox_analysis_files"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tonight — Warm-up", "Tonight — Warm-up"),
        ("A/B: Peak?", "A B Peak"),
        ("...", "djlib playlist"),
    ],
)
def test_playlist_file_names_keep_unicode(name, expected):
    assert playlist_file_name(name) == expected


def test_idle_coordinator_exits_only_without_work(application, monkeypatch):
    monkeypatch.setattr(service, "HOUSEKEEPING_SECONDS", 0.05)
    app = service.create_app(application.workspace, "idle-test", idle_exit=0.1)
    app.state.server = SimpleNamespace(should_exit=False)
    busy = {"value": True}
    monkeypatch.setattr(type(application), "busy", lambda self: busy["value"])
    with TestClient(app, base_url="http://127.0.0.1"):
        time.sleep(0.5)
        assert app.state.server.should_exit is False
        busy["value"] = False
        deadline = time.monotonic() + 3
        while not app.state.server.should_exit and time.monotonic() < deadline:
            time.sleep(0.05)
    assert app.state.server.should_exit is True


def test_push_without_a_readable_menu_checks_through_xml(
    application, library_http, audio_factory, monkeypatch
):
    collection_id = build(
        library_http, [audio_factory("menuless.wav", frequency=520)], [("Velvet Static", "Menu")]
    )["result"]["collection_id"]
    fake = FakeRekordbox(monkeypatch)
    monkeypatch.setattr(rekordbox_mac, "playlists", lambda: None)  # no track selected
    workspace = ["--workspace", str(application.workspace.root)]
    first = CliRunner().invoke(cli_app, [*workspace, "rekordbox", "push", collection_id])
    assert first.exit_code == 0, first.output
    crate = json.loads(first.stdout)["result"]["crates"][0]
    assert crate["status"] == "imported" and crate["playlist_found"] is True
    assert crate["matched"] == crate["expected"] == 1
    second = CliRunner().invoke(cli_app, [*workspace, "rekordbox", "push", collection_id])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["result"]["crates"][0]["status"] == "already_in_rekordbox"
    assert len(fake.imported) == 1


def test_usb_export_waits_for_the_right_selection_then_verifies(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    sources = [audio_factory(f"usb-{n}.wav", frequency=700 + 30 * n) for n in range(2)]
    collection_id = build(library_http, sources, [("Velvet Static", f"USB {n}") for n in range(2)])[
        "result"
    ]["collection_id"]
    fake = FakeRekordbox(monkeypatch)
    volume = tmp_path / "RICARDO_AM"
    (volume / "PIONEER" / "rekordbox").mkdir(parents=True)
    (volume / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"old")
    exported = []

    def wait_for_selection(device, target, timeout, on_wrong=None):
        on_wrong("Some other playlist")
        exported.append((device, target))
        # rekordbox copies the files and rewrites its device library.
        files = [(f"Velvet Static/{source.name}", source.read_bytes()) for source in sources]
        pdb_fixture.stick(volume, target, files)

    monkeypatch.setattr(rekordbox_mac, "wait_for_unlock", lambda timeout: None)
    monkeypatch.setattr(rekordbox_mac, "wait_for_selection", wait_for_selection)
    quick = functools.partial(usb_check.wait_for_copy, sleep=lambda seconds: None)
    monkeypatch.setattr(usb_check, "wait_for_copy", quick)
    reply = CliRunner().invoke(
        cli_app,
        [
            "--workspace",
            str(application.workspace.root),
            "rekordbox",
            "usb",
            collection_id,
            "--device",
            str(volume),
        ],
    )
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert exported == [("RICARDO_AM", "Owned")]
    assert fake.imported[0].name == "Owned.m3u8"  # pushed first because it was missing
    assert result["found"] == result["expected"] == 2 and result["missing"] == []
    assert result["in_order"] is True and result["playlist_on_device"] is True
    assert result["verified_by"] == "device_library_and_file_hashes"
    # rekordbox's key and BPM on the stick come back into the catalog for those tracks.
    assert result["analysis_from_device"]["matched"] == 2
    rows = library_http.get("/library").json()["result"]["tracks"]
    exported = [row for row in rows if row["title"].startswith("USB")]
    assert {(row["dj"]["key"], row["dj"]["bpm"]) for row in exported} == {("Am", 124.0)}
    assert result["library_updated"] is True and result["player_playback_verified"] is False


def test_set_goes_from_tracklist_to_verified_usb_in_one_command(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    sources = [audio_factory(f"set-{n}.wav", frequency=500 + 60 * n) for n in range(3)]
    build(
        library_http,
        sources,
        [
            ("Velvet Static", "Night Bus (Extended Mix)"),
            ("Nia Okoro", "Slow Burn"),
            ("Lumen", "Halo (Radio Edit)"),
        ],
    )
    tracklist = tmp_path / "friday.txt"
    tracklist.write_text(
        "Friday — Warm-up\n"
        "1. Nia Okoro - Slow Burn\n"
        "2. Lumen - Halo (Dub)\n"
        "3. Velvet Static - Night Bus (Extended Mix)\n"
        "4. Someone - Not Owned\n",
        encoding="utf-8",
    )
    fake = FakeRekordbox(monkeypatch)
    volume = tmp_path / "RICARDO_AM"
    (volume / "PIONEER" / "rekordbox").mkdir(parents=True)
    owned_in_order = [sources[1], sources[0]]

    def wait_for_selection(device, target, timeout, on_wrong=None):
        assert (device, target) == ("RICARDO_AM", "Friday — Warm-up")
        files = [(source.name, source.read_bytes()) for source in owned_in_order]
        pdb_fixture.stick(volume, target, files)

    monkeypatch.setattr(rekordbox_mac, "wait_for_unlock", lambda timeout: None)
    monkeypatch.setattr(rekordbox_mac, "wait_for_selection", wait_for_selection)
    quick = functools.partial(usb_check.wait_for_copy, sleep=lambda seconds: None)
    monkeypatch.setattr(usb_check, "wait_for_copy", quick)
    workspace = ["--workspace", str(application.workspace.root)]

    reply = CliRunner().invoke(
        cli_app, [*workspace, "set", str(tracklist), "--usb", "--device", str(volume)]
    )

    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert result["name"] == "Friday — Warm-up"
    assert (result["songs"], result["owned"]) == (4, 2)
    assert [m["label"] for m in result["missing"]] == ["Lumen - Halo (Dub)", "Someone - Not Owned"]
    assert result["missing"][0]["you_own"] == ["Radio Edit"]
    assert result["rekordbox"]["status"] == "imported"
    assert fake.imported[-1].name == "Friday — Warm-up.m3u8"
    assert result["usb"]["found"] == result["usb"]["expected"] == 2
    assert result["usb"]["in_order"] is True

    status = CliRunner().invoke(cli_app, [*workspace, "status"])
    assert status.exit_code == 0, status.output
    crates = json.loads(status.stdout)["result"]["recent_collections"]
    [crate] = [row for row in crates if row["name"] == "Friday — Warm-up"]
    assert crate["pushed_at"] and crate["usb"]["device"] == "RICARDO_AM"
    assert (crate["usb"]["found"], crate["usb"]["expected"]) == (2, 2)

    # Running it again reuses the request list, crate and playlist; nothing is imported twice.
    again = CliRunner().invoke(cli_app, [*workspace, "set", str(tracklist)])
    assert again.exit_code == 0, again.output
    second = json.loads(again.stdout)["result"]
    assert second["rekordbox"]["status"] == "already_in_rekordbox" and second["usb"] is None
    assert len(fake.imported) == 1


def test_set_without_owned_songs_builds_nothing(application, monkeypatch, tmp_path):
    tracklist = tmp_path / "nothing.txt"
    tracklist.write_text("Someone - Not Owned\nOther - Missing Too\n", encoding="utf-8")
    fake = FakeRekordbox(monkeypatch)
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "set", str(tracklist)]
    )
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert result["owned"] == 0 and result["collection_id"] is None and result["rekordbox"] is None
    assert len(result["missing"]) == 2 and fake.imported == []


def test_a_same_named_playlist_is_checked_before_it_is_trusted(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    sources = [audio_factory(f"name-{n}.wav", frequency=620 + 25 * n) for n in range(2)]
    collection_id = build(
        library_http, sources, [("Velvet Static", f"Name {n}") for n in range(2)]
    )["result"]["collection_id"]
    fake = FakeRekordbox(monkeypatch)
    fake.present.add("Owned")  # the user already has a playlist with this name
    exported = {"tracks": [(str(tmp_path / "someone-else.mp3"), "120.00")]}
    monkeypatch.setattr(
        rekordbox_mac,
        "export_collection",
        lambda destination: write_export(destination, "Owned", exported["tracks"]),
    )
    workspace = ["--workspace", str(application.workspace.root)]

    taken = CliRunner().invoke(cli_app, [*workspace, "rekordbox", "push", collection_id])
    assert taken.exit_code == 2
    error = json.loads(taken.stdout)["error"]
    assert error["code"] == "APP_PLAYLIST_NAME_TAKEN" and "Nothing was imported" in error["message"]
    assert fake.imported == []

    # The same tracks in the same order (e.g. pushed by an older djlib) are accepted.
    exported["tracks"] = [(str(source), "124.00") for source in sources]
    same = CliRunner().invoke(cli_app, [*workspace, "rekordbox", "push", collection_id])
    assert same.exit_code == 0, same.output
    assert json.loads(same.stdout)["result"]["crates"][0]["status"] == "already_in_rekordbox"
    assert fake.imported == []


def test_selection_is_reread_only_after_the_user_does_something(monkeypatch):
    clock = {"now": 0.0}
    clicks = [9.9, 19.9]  # the user clicks other playlists, then the right one
    selections = iter(["Other", "Other", "Owned"])
    probes, exported = [], []

    def selected():
        probes.append(clock["now"])
        return next(selections)

    def idle():
        latest = max([at for at in clicks if at <= clock["now"]], default=0.0)
        return clock["now"] - latest

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(rekordbox_mac.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rekordbox_mac.time, "sleep", sleep)
    monkeypatch.setattr(rekordbox_mac, "idle_seconds", idle)
    monkeypatch.setattr(rekordbox_mac, "screen_locked", lambda: False)
    monkeypatch.setattr(rekordbox_mac, "frontmost", lambda: True)
    monkeypatch.setattr(rekordbox_mac, "menu_enabled", lambda *path: True)
    monkeypatch.setattr(rekordbox_mac, "selected_playlist", selected)
    monkeypatch.setattr(rekordbox_mac, "click_menu_path", lambda *path: exported.append(path))
    wrong = []

    rekordbox_mac.wait_for_selection("RICARDO_AM", "Owned", 60, on_wrong=wrong.append)

    # One look at the start, then one after each burst of input: never a dialog per second.
    assert len(probes) == 3 and probes[0] == 0.0
    assert 9.9 <= probes[1] < 10.5 and 19.9 <= probes[2] < 20.5
    assert wrong == ["Other"]
    assert exported == [("Playlist", "Export Playlist", "RICARDO_AM")]
