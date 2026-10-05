"""`djlib rekordbox push/pull` against a simulated rekordbox UI and its real XML format."""

import json
from pathlib import Path
from xml.sax.saxutils import quoteattr

from typer.testing import CliRunner

from djlib.domain.errors import AppError
from djlib.exporting.native_rekordbox import playlist_report
from djlib.interfaces.cli import app as cli_app
from djlib.native import rekordbox_mac
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


def test_playlist_report_reads_both_key_types(tmp_path):
    tracks = [(str(tmp_path / "a.mp3"), "124.00"), (str(tmp_path / "b.mp3"), "0.00")]
    for key_type in ("0", "1"):
        xml = write_export(tmp_path / f"x{key_type}.xml", "Set 0", tracks, key_type)
        report = playlist_report(xml, "Set 0", [p for p, _ in tracks] + [str(tmp_path / "c.mp3")])
        assert report["playlist_found"] and report["entries"] == 2
        assert (report["expected"], report["matched"], report["analyzed"]) == (3, 2, 1)
        assert report["missing_paths"] == [str(tmp_path / "c.mp3")]
    assert playlist_report(xml, "Other", [])["playlist_found"] is False


def test_push_imports_verifies_and_pulls_analysis(
    application, library_http, audio_factory, monkeypatch
):
    sources = [audio_factory(f"push-{n}.wav", frequency=300 + 40 * n) for n in range(2)]
    collection_id = build(
        library_http, sources, [("Velvet Static", f"Push {n}") for n in range(2)]
    )["result"]["collection_id"]
    imported: list[Path] = []

    def import_playlist(playlist: Path):
        imported.append(playlist)

    def export_collection(destination: Path) -> Path:
        # rekordbox lists the M3U's files and names the playlist after the file.
        lines = imported[-1].read_text(encoding="utf-8").splitlines()
        paths = [line for line in lines if line and not line.startswith("#")]
        return write_export(destination, imported[-1].stem, [(p, "126.00") for p in paths])

    monkeypatch.setattr(rekordbox_mac, "ensure_supported", lambda: None)
    monkeypatch.setattr(rekordbox_mac, "import_playlist", import_playlist)
    monkeypatch.setattr(rekordbox_mac, "export_collection", export_collection)
    reply = CliRunner().invoke(
        cli_app,
        [
            "--workspace",
            str(application.workspace.root),
            "rekordbox",
            "push",
            collection_id,
            "--wait",
            "0",
        ],
    )
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert imported[0].name == "Owned.m3u8"
    assert result["playlist_found"] and result["matched"] == result["expected"] == 2
    assert result["analyzed"] == 2 and result["analysis_import"]["updated"] == 2
    assert result["database_modified_directly"] is False
    rows = library_http.get("/library").json()["result"]["tracks"]
    assert {row["dj"]["bpm"] for row in rows if row["title"].startswith("Push")} == {126.0}


def test_push_explains_the_macos_permission(application, monkeypatch):
    def refuse():
        raise AppError("APP_AUTOMATION_NOT_ALLOWED", "Allow your terminal app.")

    monkeypatch.setattr(rekordbox_mac, "ensure_supported", refuse)
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "rekordbox", "push", "x"]
    )
    assert reply.exit_code == 2
    assert json.loads(reply.stdout)["error"]["code"] == "APP_AUTOMATION_NOT_ALLOWED"
