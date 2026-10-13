"""Shazam history: every place it lives, merged in the order you heard the songs, as a set."""

import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from djlib.domain.errors import AppError
from djlib.interfaces import shazam_cli
from djlib.interfaces.cli import app as cli_app
from djlib.native import rekordbox_mac
from djlib.sources import shazam_history
from djlib.workspace import Workspace
from tests.test_headline_workflow import build
from tests.test_rekordbox_push import FakeRekordbox

CORE_DATA = 978307200


def apple_time(iso: str) -> float:
    """Core Data seconds since 2001, as Shazam stores them."""
    return datetime.fromisoformat(iso).timestamp() - CORE_DATA


@pytest.fixture
def home():
    """The isolated home the tests' Shazam history lives in (set in conftest)."""
    return Path(os.environ[shazam_history.HOME_ENV])


def mac_path(home: Path) -> Path:
    path = home / "Library/Application Support/com.apple.shazamd/ShazamLibrary.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def mac_db(home: Path) -> sqlite3.Connection:
    """The Mac's Music Recognition library, in WAL mode like the real one."""
    db = sqlite3.connect(mac_path(home))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS ZSHTRACKMO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, "
        "Z_OPT INTEGER, ZDATE TIMESTAMP, ZTITLE VARCHAR, ZSUBTITLE VARCHAR, ZISRC VARCHAR, "
        "ZSHAZAMKEY VARCHAR, ZAPPLEMUSICID VARCHAR, ZLATITUDE FLOAT, ZLONGITUDE FLOAT)"
    )
    return db


def add(db: sqlite3.Connection, *shazams: tuple) -> None:
    """(when, artist, title[, isrc[, latitude, longitude]]) rows, committed."""
    for at, artist, title, *extra in shazams:
        isrc = extra[0] if extra else None
        place = extra[1:3] if len(extra) >= 3 else (None, None)
        db.execute(
            "INSERT INTO ZSHTRACKMO (Z_ENT, Z_OPT, ZDATE, ZTITLE, ZSUBTITLE, ZISRC, ZSHAZAMKEY, "
            "ZAPPLEMUSICID, ZLATITUDE, ZLONGITUDE) VALUES (1, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (apple_time(at), title, artist, isrc, f"key-{title}", 1500000000, *place),
        )
    db.commit()


def mac_history(home: Path, *shazams: tuple) -> None:
    db = mac_db(home)
    add(db, *shazams)
    db.close()


def legacy_app(home: Path, *tags: tuple) -> Path:
    """The older Shazam app for Mac: tag results, with one row per credited artist."""
    folder = home / "Library/Group Containers/4GW6U2AJ3J.group.com.shazam"
    path = folder / "com.shazam.mac.Shazam" / "ShazamDataModel.sqlite"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE ZSHTAGRESULTMO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, "
        "ZDATE TIMESTAMP, ZTRACKNAME VARCHAR, ZTRACKKEY VARCHAR)"
    )
    db.execute(
        "CREATE TABLE ZSHARTISTMO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, "
        "ZTAGRESULT INTEGER, ZNAME VARCHAR)"
    )
    for at, artists, title in tags:
        cursor = db.execute(
            "INSERT INTO ZSHTAGRESULTMO (Z_ENT, Z_OPT, ZDATE, ZTRACKNAME, ZTRACKKEY) "
            "VALUES (2, 1, ?, ?, ?)",
            (apple_time(at), title, f"legacy-{title}"),
        )
        for artist in artists:
            db.execute(
                "INSERT INTO ZSHARTISTMO (Z_ENT, Z_OPT, ZTAGRESULT, ZNAME) VALUES (3, 1, ?, ?)",
                (cursor.lastrowid, artist),
            )
    db.commit()
    db.close()
    return path


def iphone_backup(folder: Path, *tags: tuple, encrypted: bool = False) -> Path:
    """An unencrypted Finder backup: Manifest.db names the Shazam app's library file."""
    folder.mkdir(parents=True)
    with (folder / "Manifest.plist").open("wb") as stream:
        plistlib.dump({"IsEncrypted": encrypted}, stream)
    relative = "Documents/ShazamDataModel.sqlite"
    file_id = hashlib.sha1(f"AppDomain-com.shazam.Shazam-{relative}".encode()).hexdigest()
    stored = folder / file_id[:2] / file_id
    stored.parent.mkdir()
    db = sqlite3.connect(stored)
    db.execute(
        "CREATE TABLE ZTRACKMO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, "
        "ZNAME VARCHAR, ZARTISTNAME VARCHAR, ZAPPLEMUSICID INTEGER, ZISRC VARCHAR)"
    )
    db.execute(
        "CREATE TABLE ZTAGMO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, "
        "ZDATE TIMESTAMP, ZTRACK INTEGER, ZLATITUDE FLOAT, ZLONGITUDE FLOAT)"
    )
    for at, artist, title in tags:
        track = db.execute(
            "INSERT INTO ZTRACKMO (Z_ENT, Z_OPT, ZNAME, ZARTISTNAME, ZAPPLEMUSICID) "
            "VALUES (4, 1, ?, ?, 1440000000)",
            (title, artist),
        ).lastrowid
        db.execute(
            "INSERT INTO ZTAGMO (Z_ENT, Z_OPT, ZDATE, ZTRACK, ZLATITUDE, ZLONGITUDE) "
            "VALUES (5, 1, ?, ?, 52.52, 13.405)",
            (apple_time(at), track),
        )
    db.commit()
    db.close()
    manifest = sqlite3.connect(folder / "Manifest.db")
    manifest.execute(
        "CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, "
        "flags INTEGER, file BLOB)"
    )
    manifest.executemany(
        "INSERT INTO Files VALUES (?, ?, ?, 1, NULL)",
        [
            (file_id, "AppDomain-com.shazam.Shazam", relative),
            ("0" * 40, "HomeDomain", "Library/Preferences/com.apple.Music.plist"),
        ],
    )
    manifest.commit()
    manifest.close()
    return stored


def titles(records: list[dict]) -> list[str]:
    return [item["title"] for item in records]


# -- reading each source -----------------------------------------------------------------------


def test_the_mac_history_includes_shazams_still_in_the_write_ahead_log(home, tmp_path):
    path = mac_path(home)
    db = mac_db(home)
    add(db, ("2026-10-01T21:14:00+00:00", "Nia Okoro", "Slow Burn", "gbaaa2600001"))
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("PRAGMA wal_autocheckpoint=0")
    add(db, ("2026-10-02T23:05:00+00:00", "Velvet Static", "Night Bus", None, 52.52, 13.405))
    try:
        # The newest Shazam is only in the -wal file, as when shazamd is mid-write.
        alone = tmp_path / "alone.sqlite"
        shutil.copyfile(path, alone)
        check = sqlite3.connect(alone)
        assert [row[0] for row in check.execute("SELECT ZTITLE FROM ZSHTRACKMO")] == ["Slow Burn"]
        check.close()
        live = (path.read_bytes(), Path(f"{path}-wal").read_bytes())

        loaded = shazam_history.load()

        assert titles(loaded["records"]) == ["Slow Burn", "Night Bus"]
        first, second = loaded["records"]
        assert first == {
            "title": "Slow Burn",
            "artist": "Nia Okoro",
            "shazamed_at": "2026-10-01T21:14:00+00:00",
            "isrc": "GBAAA2600001",
            "shazam_key": "key-Slow Burn",
            "apple_music_id": "1500000000",
            "location": None,
            "source": "mac",
            "count": 1,
            "last_shazamed_at": "2026-10-01T21:14:00+00:00",
        }
        assert second["location"] == "52.5200, 13.4050"
        assert loaded["sources"] == [
            {
                "source": "mac",
                "label": "Music Recognition on this Mac",
                "path": str(path),
                "songs": 2,
            }
        ]
        # Read from a copy: the live files are unchanged and Shazam can keep writing.
        assert (path.read_bytes(), Path(f"{path}-wal").read_bytes()) == live
        add(db, ("2026-10-03T01:00:00+00:00", "Lumen", "Halo"))
    finally:
        db.close()
    assert titles(shazam_history.load()["records"]) == ["Slow Burn", "Night Bus", "Halo"]


def test_the_older_mac_app_joins_its_artists_and_merges_with_the_mac_history(home):
    mac_history(home, ("2026-10-02T12:00:00+00:00", "Velvet Static & Nia Okoro", "Rain"))
    legacy_app(
        home,
        ("2019-03-02T21:14:00+00:00", ["Overmono"], "Gem Lingo"),
        ("2019-03-03T01:00:00+00:00", ["Nia Okoro", "Velvet Static"], "Rain"),
    )
    loaded = shazam_history.load()
    assert [s["source"] for s in loaded["sources"]] == ["mac", "mac_app"]
    assert [(item["artist"], item["title"], item["count"]) for item in loaded["records"]] == [
        ("Overmono", "Gem Lingo", 1),
        # Same credited artists in another order: one song, first heard in 2019.
        ("Nia Okoro, Velvet Static", "Rain", 2),
    ]
    rain = loaded["records"][1]
    assert rain["shazamed_at"] == "2019-03-03T01:00:00+00:00"
    assert rain["last_shazamed_at"] == "2026-10-02T12:00:00+00:00"
    assert loaded["records"][0]["shazam_key"] == "legacy-Gem Lingo"


def test_an_iphone_backup_is_read_through_its_manifest(home, tmp_path):
    backup = tmp_path / "Backup" / "00008110-001A"
    stored = iphone_backup(
        backup,
        ("2026-09-20T22:00:00+00:00", "Lumen", "Halo (Extended Mix)"),
        ("2026-09-19T23:30:00+00:00", "Overmono", "Gem Lingo"),
    )
    for spec in (str(backup), str(backup.parent), str(stored)):
        source, since = shazam_history.parse_spec(f"shazam:{spec}")
        assert (source, since) == (spec, None)
        loaded = shazam_history.load(source)
        assert titles(loaded["records"]) == ["Gem Lingo", "Halo (Extended Mix)"]
    loaded = shazam_history.load(str(backup))
    assert loaded["sources"][0]["source"] == "iphone_backup"
    item = loaded["records"][1]
    assert (item["artist"], item["apple_music_id"], item["location"]) == (
        "Lumen",
        "1440000000",
        "52.5200, 13.4050",
    )


def test_encrypted_and_empty_backups_say_what_to_do(tmp_path):
    iphone_backup(tmp_path / "locked", encrypted=True)
    with pytest.raises(AppError) as locked:
        shazam_history.load(str(tmp_path / "locked"))
    assert locked.value.code == "SHAZAM_BACKUP_ENCRYPTED" and "iCloud" in locked.value.message
    with pytest.raises(AppError) as empty:
        iphone_backup(tmp_path / "empty")
        shazam_history.load(str(tmp_path / "empty"))
    assert empty.value.code == "SHAZAM_HISTORY_NOT_FOUND"
    (tmp_path / "plain").mkdir()
    with pytest.raises(AppError) as plain:
        shazam_history.load(str(tmp_path / "plain"))
    assert plain.value.code == "INPUT_INVALID" and "Manifest.db" in plain.value.message


def test_csv_exports_from_shazam_and_elsewhere(tmp_path):
    export = tmp_path / "shazamlibrary.csv"
    export.write_text(
        "Shazam Library\n"
        "Index,TagTime,Title,Artist,URL,TrackKey\n"
        "1,2019-03-02T21:14:00Z,Gem Lingo,Overmono,https://www.shazam.com/track/1,123\n"
        "2,2019-03-01T20:00:00Z,Night Bus (Extended Mix),Velvet Static,https://x,456\n"
        "3,2019-02-28T20:00:00Z,,Nobody,https://x,789\n",
        encoding="utf-8",
    )
    loaded = shazam_history.load(str(export))
    assert loaded["sources"][0]["source"] == "csv"
    assert [(i["artist"], i["title"], i["shazam_key"]) for i in loaded["records"]] == [
        ("Velvet Static", "Night Bus (Extended Mix)", "456"),
        ("Overmono", "Gem Lingo", "123"),
    ]
    assert loaded["records"][1]["shazamed_at"] == "2019-03-02T21:14:00+00:00"
    plain = tmp_path / "plain.csv"
    plain.write_text("Artist;Title\nLumen;Halo\nNia Okoro;Slow Burn\n", encoding="utf-8")
    records = shazam_history.load(str(plain))["records"]
    assert [(i["artist"], i["title"], i["shazamed_at"]) for i in records] == [
        ("Lumen", "Halo", None),
        ("Nia Okoro", "Slow Burn", None),
    ]
    twice = tmp_path / "twice.csv"
    twice.write_text("Artist,Title\nLumen,Halo\nLumen,Halo\n", encoding="utf-8")
    loaded = shazam_history.load(str(twice))
    assert (loaded["sources"][0]["songs"], loaded["total"], len(loaded["records"])) == (1, 1, 1)
    other = tmp_path / "other.csv"
    other.write_text("Name,Size\nx,1\n", encoding="utf-8")
    with pytest.raises(AppError) as columns:
        shazam_history.load(str(other))
    assert columns.value.code == "INPUT_INVALID" and "Artist" in columns.value.message


def music(monkeypatch, returncode=0, stdout="", stderr=""):
    """Answer JXA for Music without running osascript; returns the scripts it was given."""
    scripts = []

    def run(script):
        scripts.append(script)
        return subprocess.CompletedProcess(["osascript"], returncode, stdout, stderr)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shazam_history, "run_music_script", run)
    return scripts


def test_apple_music_my_shazam_tracks(monkeypatch):
    tracks = [
        {"name": "Halo", "artist": "Lumen", "added": "2026-10-02T21:00:00.000Z"},
        {"name": "Gem Lingo", "artist": "Overmono", "added": "2026-10-01T20:00:00.000Z"},
        {"name": "", "artist": "Nobody", "added": None},
    ]
    scripts = music(monkeypatch, stdout=json.dumps(tracks))
    source, since = shazam_history.parse_spec("shazam:apple-music")
    loaded = shazam_history.load(source, since)
    assert "My Shazam Tracks" in scripts[0] and "dateAdded" in scripts[0]
    assert titles(loaded["records"]) == ["Gem Lingo", "Halo"]
    assert loaded["records"][1]["shazamed_at"] == "2026-10-02T21:00:00+00:00"
    assert loaded["sources"][0]["source"] == "apple_music"

    for stderr, code in (
        ("execution error: Error: Error: Can't get object. (-1728)", "SHAZAM_PLAYLIST_NOT_FOUND"),
        ("Not authorized to send Apple events to Music. (-1743)", "SHAZAM_MUSIC_NOT_ALLOWED"),
        ("something else", "SHAZAM_MUSIC_FAILED"),
    ):
        music(monkeypatch, returncode=1, stderr=stderr)
        with pytest.raises(AppError) as failed:
            shazam_history.load(shazam_history.APPLE_MUSIC)
        assert failed.value.code == code
    assert failed.value.retryable  # Music didn't answer: try again
    music(monkeypatch, stdout="not JSON")
    with pytest.raises(AppError) as unreadable:
        shazam_history.read_apple_music()
    assert unreadable.value.code == "SHAZAM_MUSIC_FAILED"
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(AppError) as elsewhere:
        shazam_history.read_apple_music()
    # Music's own codes: rekordbox's (APP_AUTOMATION_*) have titles that name rekordbox.
    assert elsewhere.value.code == "SHAZAM_MUSIC_UNSUPPORTED"


# -- merging, filters and specs ----------------------------------------------------------------


def shazam(at, artist, title, isrc=None):
    return shazam_history.record(title, artist, at, "mac", isrc=isrc)


def test_songs_are_merged_by_isrc_or_equivalent_labels_oldest_first():
    merged = shazam_history.merge(
        [
            shazam("2026-10-02T10:00:00+00:00", "Lumen", "Halo", isrc="GB-AAA-26-00001"),
            shazam("2026-10-01T10:00:00+00:00", "Lumen", "Halo (Original Mix)"),
            shazam("2026-10-03T10:00:00+00:00", "LUMEN feat. Ana", "Halo", isrc="GBAAA2600001"),
            shazam("2026-10-01T09:00:00+00:00", "Lumen", "Halo (Extended Mix)"),
            shazam(None, "Nia Okoro", "Slow Burn"),
            shazam("2026-09-30T10:00:00+00:00", "Velvet Static & Nia Okoro", "Rain"),
            shazam("2026-10-04T10:00:00+00:00", "Nia Okoro, Velvet Static", "Rain"),
        ]
    )
    assert [(i["title"], i["count"]) for i in merged] == [
        ("Rain", 2),
        ("Halo (Extended Mix)", 1),  # another version is another song
        ("Halo (Original Mix)", 3),  # "(Original Mix)" and the same ISRC are the same song
        ("Slow Burn", 1),  # no time: last
    ]
    halo = merged[2]
    assert halo["shazamed_at"] == "2026-10-01T10:00:00+00:00"
    assert halo["last_shazamed_at"] == "2026-10-03T10:00:00+00:00"
    assert halo["isrc"] == "GB-AAA-26-00001"
    assert merged[0]["last_shazamed_at"] == "2026-10-04T10:00:00+00:00"
    assert shazam_history.newest(merged) == "2026-10-04T10:00:00+00:00"


def test_set_items_split_the_mix_name_like_tracklists():
    labels = shazam_cli.labels
    assert labels({"artist": "Lumen", "title": "Halo (Extended Mix)"}) == (
        "Lumen",
        "Halo",
        "Extended Mix",
    )
    assert labels({"artist": "Lumen", "title": "Halo - Radio Edit"}) == (
        "Lumen",
        "Halo",
        "Radio Edit",
    )
    assert labels({"artist": "The Streets", "title": "Turn the Page (Overmono Remix)"})[2] == (
        "Overmono Remix"
    )
    assert labels({"artist": "Lumen", "title": "Halo (feat. Ana)"}) == (
        "Lumen",
        "Halo (feat. Ana)",
        "",
    )
    assert labels({"artist": "Lumen", "title": "Halo [Defected]"}) == (
        "Lumen",
        "Halo [Defected]",
        "",
    )
    assert labels({"artist": " Lumen ", "title": "Halo  "}) == ("Lumen", "Halo", "")
    note = shazam_cli.note({"shazamed_at": "2026-10-02T21:14:00+00:00", "location": "Berlin"})
    assert note == f"Shazamed {shazam_history.local_time('2026-10-02T21:14:00+00:00')} · Berlin"
    assert shazam_history.local_time("2026-10-02T21:14:00+00:00")[:9] == "2026-10-0"


def test_specs_dates_and_new(home, tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.root.mkdir()
    parse = shazam_history.parse_spec
    assert parse("shazam") == (None, None) and parse("Shazam:") == (None, None)
    assert parse("shazam:new") == (None, "new")
    assert parse("shazam:2026-10-01") == (None, "2026-10-01")
    assert parse("shazam:apple-music") == ("apple-music", None)
    for wrong, code in (
        ("shazam:yesterday", "INPUT_INVALID"),
        ("shazam:~/nowhere/history.csv", "FILE_UNAVAILABLE"),
        ("tracks.txt", "INPUT_INVALID"),
    ):
        with pytest.raises(AppError) as error:
            parse(wrong)
        assert error.value.code == code
    assert shazam_cli.is_spec("shazam:new") and shazam_cli.is_spec("SHAZAM")
    assert not shazam_cli.is_spec("https://youtu.be/x") and not shazam_cli.is_spec("set.txt")

    mac_history(
        home,
        ("2026-09-28T12:00:00+00:00", "Overmono", "Gem Lingo"),
        ("2026-10-06T12:00:00+00:00", "Lumen", "Halo"),
        ("2026-10-07T12:00:00+00:00", "Nia Okoro", "Slow Burn"),
    )
    dated = shazam_history.load(None, "2026-10-02", workspace)
    assert titles(dated["records"]) == ["Halo", "Slow Burn"] and dated["total"] == 3
    assert dated["name"] == "Shazam since 2026-10-02" and dated["since"]
    with pytest.raises(AppError) as wrong:
        shazam_history.load(None, "2026-13-45", workspace)
    assert wrong.value.code == "INPUT_INVALID"

    # Nothing imported yet: everything is new.
    assert titles(shazam_history.load(None, "new", workspace)["records"]) == [
        "Gem Lingo",
        "Halo",
        "Slow Burn",
    ]
    shazam_history.remember_import(
        workspace,
        {
            "newest_shazamed_at": "2026-10-06T12:00:00+00:00",
            "songs": 2,
            "name": "S",
            "sources": [{"source": "mac"}],
        },
    )
    mac_history(home, ("2026-10-08T12:00:00+00:00", "Overmono", "Gem Lingo"))
    fresh = shazam_history.load(None, "new", workspace)
    # Shazamed again after the last import: new again, with its new time.
    assert titles(fresh["records"]) == ["Slow Burn", "Gem Lingo"]
    assert fresh["records"][1]["shazamed_at"] == "2026-10-08T12:00:00+00:00"
    assert fresh["new"] is True and fresh["last_import"]["songs"] == 2


def test_words_and_dates_win_over_files_of_the_same_name(home, tmp_path, monkeypatch):
    # A music folder with folders named like the words and dates shazam: takes.
    for name in ("new", "music", "2026-10-01", "Backup"):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path)
    parse = shazam_history.parse_spec
    assert parse("shazam:new") == (None, "new")
    assert parse("shazam:music") == ("apple-music", None)
    assert parse("shazam:2026-10-01") == (None, "2026-10-01")
    # Written as a path, it is the folder.
    assert parse("shazam:./new") == ("./new", None)
    assert parse(f"shazam:{tmp_path / 'new'}") == (str(tmp_path / "new"), None)
    assert parse("shazam:Backup") == ("Backup", None)  # no word of that name
    (tmp_path / "2026-10-01.csv").write_text("Artist,Title\nLumen,Halo\n", encoding="utf-8")
    assert parse("shazam:2026-10-01.csv") == ("2026-10-01.csv", None)
    with pytest.raises(AppError) as missing:
        parse("shazam:2026-10-02.csv")
    assert missing.value.code == "FILE_UNAVAILABLE"
    # Dates written the European or US way are not paths.
    for date in ("01/10/2026", "1.10.26", "01-10-2026", "2026/10/01"):
        with pytest.raises(AppError) as written:
            parse(f"shazam:{date}")
        assert written.value.code == "INPUT_INVALID", date
        assert "YYYY-MM-DD, e.g. shazam:2026-10-01" in written.value.message

    # --from never means new or a date, so there the folder counts.
    mac_history(home, ("2026-10-02T21:14:00+00:00", "Lumen", "Halo"))
    shutil.copyfile(mac_path(home), tmp_path / "new" / "ShazamLibrary.sqlite")
    listed = invoke(Workspace(tmp_path / "workspace"), "shazam", "list", "--from", "new")
    assert listed.exit_code == 0, listed.output
    assert titles(json.loads(listed.stdout)["result"]["records"]) == ["Halo"]


def test_a_long_history_comes_in_lists_of_1000(home, tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.root.mkdir()
    start = datetime.fromisoformat("2025-01-01T12:00:00+00:00").timestamp()
    mac_history(
        home,
        *(
            (datetime.fromtimestamp(start + 3600 * n).isoformat(), f"Artist {n}", f"Song {n}")
            for n in range(1003)
        ),
    )
    body, warnings, summary = shazam_cli.set_request("shazam", None, workspace)
    assert len(body.items) == 1000 and body.items[0].title == "Song 0"
    assert (summary["songs"], summary["remaining"]) == (1000, 3)
    assert any("shazam:new" in warning for warning in warnings)
    shazam_history.remember_import(workspace, summary)
    body, _, summary = shazam_cli.set_request("shazam:new", None, workspace)
    assert [item.title for item in body.items] == ["Song 1000", "Song 1001", "Song 1002"]
    assert summary["remaining"] == 0
    again, _, _ = shazam_cli.set_request("shazam", None, workspace)
    assert (
        again.idempotency_key
        == shazam_cli.set_request("shazam", None, workspace)[0].idempotency_key
    )


def test_the_next_list_skips_repeats_of_songs_a_full_list_already_checked(home, tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.root.mkdir()
    start = datetime.fromisoformat("2025-01-01T12:00:00+00:00").timestamp()

    def hour(n):
        return datetime.fromtimestamp(start + 3600 * n).isoformat()

    mac_history(
        home,
        *((hour(n), f"Artist {n}", f"Song {n}") for n in range(2003)),
        # Shazamed again before the first import, after the songs that end lists 1 and 2.
        (hour(1500.5), "Artist 0", "Song 0"),
        (hour(2001.5), "Artist 1200", "Song 1200"),
        (hour(2003), "Artist 5", "Song 5"),  # the newest Shazam of all
    )
    body, warnings, summary = shazam_cli.set_request("shazam", None, workspace)
    assert body.items[-1].title == "Song 999"
    assert any("for the other 1003" in warning for warning in warnings)
    shazam_history.remember_import(workspace, summary)
    assert shazam_history.status(workspace)["new_since_last_import"] == 1003

    body, warnings, summary = shazam_cli.set_request("shazam:new", None, workspace)
    # Song 0 and Song 5 were in the first list, so their repeats don't bring them back.
    assert [item.title for item in body.items] == [f"Song {n}" for n in range(1000, 2000)]
    assert any("for the other 3" in warning for warning in warnings)
    shazam_history.remember_import(workspace, summary)
    body, _, summary = shazam_cli.set_request("shazam:new", None, workspace)
    assert [item.title for item in body.items] == ["Song 2000", "Song 2001", "Song 2002"]
    shazam_history.remember_import(workspace, summary)

    # Song 5's repeat is newer than any song in the last list, and still not new.
    assert shazam_history.status(workspace)["new_since_last_import"] == 0
    with pytest.raises(AppError) as nothing:
        shazam_cli.set_request("shazam:new", None, workspace)
    assert nothing.value.code == "SHAZAM_NOTHING_NEW"
    # Shazamed again after the import: new again.
    mac_history(home, (hour(2004), "Artist 5", "Song 5"))
    body, _, summary = shazam_cli.set_request("shazam:new", None, workspace)
    assert [item.title for item in body.items] == ["Song 5"]
    shazam_history.remember_import(workspace, summary)
    assert shazam_history.last_import(workspace)["held"] == []


def test_a_long_list_from_apple_music_or_a_file_keeps_your_newest_shazams(
    home, tmp_path, monkeypatch
):
    workspace = Workspace(tmp_path / "workspace")
    workspace.root.mkdir()
    start = datetime.fromisoformat("2025-01-01T12:00:00+00:00").timestamp()
    tracks = [
        {
            "name": f"Song {n}",
            "artist": f"Artist {n}",
            "added": datetime.fromtimestamp(start + 3600 * n).astimezone().isoformat(),
        }
        for n in range(1500)
    ]
    music(monkeypatch, stdout=json.dumps(tracks))
    # shazam:new reads only this Mac's history, so it can't continue these lists.
    body, warnings, summary = shazam_cli.set_request("shazam:apple-music", None, workspace)
    assert len(body.items) == 1000
    assert (body.items[0].title, body.items[-1].title) == ("Song 500", "Song 1499")
    assert (summary["songs"], summary["remaining"]) == (1000, 500)
    assert summary["newest_shazamed_at"] == shazam_history.when(tracks[-1]["added"])
    assert not any("shazam:new" in warning for warning in warnings)
    assert any("your 500 oldest Shazams" in warning for warning in warnings)
    assert not shazam_history.from_this_mac(summary)

    undated = tmp_path / "undated.csv"
    undated.write_text(
        "Artist,Title\n" + "".join(f"Artist {n},Song {n}\n" for n in range(1003)),
        encoding="utf-8",
    )
    body, warnings, summary = shazam_cli.set_request(f"shazam:{undated}", None, workspace)
    assert (body.items[0].title, body.items[-1].title) == ("Song 3", "Song 1002")
    assert any("the first 3 songs in undated.csv" in warning for warning in warnings)
    assert not any("shazam:new" in warning for warning in warnings)


def test_only_imports_from_this_macs_history_move_the_shazam_new_mark(home, tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "workspace")
    workspace.root.mkdir()
    mac_history(
        home,
        ("2026-10-01T12:00:00+00:00", "Lumen", "Halo"),
        ("2026-10-05T12:00:00+00:00", "Nia Okoro", "Slow Burn"),
    )

    def mark():
        return shazam_history.last_import(workspace)["newest_shazamed_at"]

    _, _, summary = shazam_cli.set_request("shazam", None, workspace)
    assert shazam_history.from_this_mac(summary)
    shazam_history.remember_import(workspace, summary, "request_" + "a" * 32)
    assert mark() == "2026-10-05T12:00:00+00:00"

    old = tmp_path / "old.csv"
    old.write_text(
        "TagTime,Title,Artist\n2019-01-01T20:00:00Z,Gem Lingo,Overmono\n", encoding="utf-8"
    )
    undated = tmp_path / "undated.csv"
    undated.write_text("Artist,Title\nVelvet Static,Night Bus\n", encoding="utf-8")
    music(monkeypatch, stdout=json.dumps([{"name": "Rain", "artist": "Lumen", "added": None}]))
    for spec in (f"shazam:{old}", f"shazam:{undated}", "shazam:apple-music"):
        _, _, summary = shazam_cli.set_request(spec, None, workspace)
        assert shazam_history.remember_import(workspace, summary, "request_" + "b" * 32) is None
        assert mark() == "2026-10-05T12:00:00+00:00", spec

    # An older Mac import (an earlier date range) never moves the mark back.
    _, _, older = shazam_cli.set_request("shazam:2026-09-01", None, workspace)
    shazam_history.remember_import(
        workspace, {**older, "newest_shazamed_at": "2026-10-01T12:00:00+00:00"}
    )
    assert mark() == "2026-10-05T12:00:00+00:00"
    assert shazam_history.status(workspace)["new_since_last_import"] == 0
    with pytest.raises(AppError) as nothing:
        shazam_cli.set_request("shazam:new", None, workspace)
    assert nothing.value.code == "SHAZAM_NOTHING_NEW"


def test_no_history_explains_how_to_turn_on_sync(home, monkeypatch):
    with pytest.raises(AppError) as missing:
        shazam_history.load()
    error = missing.value
    assert error.code == "SHAZAM_HISTORY_NOT_FOUND"
    # One step per line, numbered, so the terminal shows a list rather than a paragraph.
    lines = error.message.splitlines()
    assert lines[0] == "No Shazam history was found on this Mac. To bring your Shazams here:"
    for line, words in zip(
        lines[1:],
        (
            "1. On your iPhone: Settings → [your name] → iCloud → turn on Shazam / Music "
            "Recognition",
            "2. On this Mac: System Settings → Apple Account → iCloud → See All → turn on "
            "Music Recognition",
            "3. Open Control Center → Music Recognition once",
            "Or run djlib set shazam:apple-music",
            "Or run djlib set shazam:BACKUP_OR_CSV",
        ),
        strict=True,
    ):
        assert line.startswith(words), line
    assert error.details["setup"] == shazam_history.SETUP

    mac_db(home).close()  # Music Recognition is on but nothing has synced yet
    with pytest.raises(AppError) as empty:
        shazam_history.load()
    assert empty.value.code == "SHAZAM_HISTORY_NOT_FOUND"
    assert empty.value.message.startswith("Music Recognition on this Mac has no songs yet.")

    def refused(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    mac_history(home, ("2026-10-01T12:00:00+00:00", "Lumen", "Halo"))
    monkeypatch.setattr(shazam_history.shutil, "copyfile", refused)
    with pytest.raises(AppError) as denied:
        shazam_history.load()
    assert denied.value.code == "SHAZAM_ACCESS_DENIED"
    assert "Full Disk Access" in denied.value.message
    summary = shazam_history.mac_summary()
    assert summary["problem"] == "SHAZAM_ACCESS_DENIED"
    assert summary["fix"] == shazam_history.FULL_DISK_SETTINGS


# -- through the CLI ---------------------------------------------------------------------------


def invoke(workspace, *arguments, pretty=False):
    env = {"DJLIB_OUTPUT": "pretty" if pretty else None, "COLUMNS": "120", "NO_COLOR": "1"}
    return CliRunner(env=env).invoke(
        cli_app, ["--workspace", str(workspace.root), *map(str, arguments)]
    )


def test_set_shazam_checks_your_shazams_and_then_only_new_ones(
    application, library_http, audio_factory, monkeypatch, home, tmp_path
):
    owned = [audio_factory(f"shazam-{n}.wav", frequency=350 + 50 * n) for n in range(3)]
    build(
        library_http,
        owned,
        [
            ("Velvet Static", "Night Bus (Extended Mix)"),
            ("Nia Okoro", "Slow Burn"),
            ("Lumen", "Halo"),
        ],
    )
    mac_history(
        home,
        ("2026-10-01T21:14:00+00:00", "Nia Okoro", "Slow Burn", "GBAAA2600001", 52.52, 13.405),
        ("2026-10-02T23:05:00+00:00", "Velvet Static", "Night Bus (Extended Mix)"),
        ("2026-10-03T01:30:00+00:00", "Someone", "Not Owned"),
        ("2026-10-03T02:00:00+00:00", "Nia Okoro", "Slow Burn", "GBAAA2600001"),
    )
    fake = FakeRekordbox(monkeypatch)
    workspace = application.workspace

    reply = invoke(workspace, "set", "shazam")

    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert result["name"] == "Shazam"
    assert (result["songs"], result["owned"]) == (3, 2)
    assert [m["label"] for m in result["missing"]] == ["Someone - Not Owned"]
    assert result["rekordbox"]["status"] == "imported"
    assert fake.imported[-1].name == "Shazam.m3u8"
    assert result["source"]["provider"] == "Shazam"
    assert result["source"]["title"] == "Music Recognition on this Mac, 3 songs"
    summary = result["shazam"]
    assert (summary["songs"], summary["songs_in_history"]) == (3, 3)
    assert summary["sources"][0]["songs"] == 3  # songs, not the 4 Shazams
    assert summary["newest_shazamed_at"] == "2026-10-03T02:00:00+00:00"
    request = library_http.get(f"/requests/{result['request_id']}").json()["result"]
    inputs = [item["input"] for item in request["items"]]
    # In the order you heard them; the mix name is the version, the Shazam time a note.
    assert [(i["artist"], i["title"], i["version"]) for i in inputs] == [
        ("Nia Okoro", "Slow Burn", ""),
        ("Velvet Static", "Night Bus", "Extended Mix"),
        ("Someone", "Not Owned", ""),
    ]
    when = shazam_history.local_time("2026-10-01T21:14:00+00:00")
    assert inputs[0]["notes"] == f"Shazamed {when} · 52.5200, 13.4050"
    remembered = json.loads((workspace.root / "shazam.json").read_text(encoding="utf-8"))
    assert remembered["last_import"]["newest_shazamed_at"] == "2026-10-03T02:00:00+00:00"
    assert remembered["last_import"]["request_id"] == result["request_id"]

    # Later that night: two more Shazams, one of them a song you own.
    mac_history(
        home,
        ("2026-10-04T00:10:00+00:00", "Lumen", "Halo"),
        ("2026-10-04T00:40:00+00:00", "Lumen", "Halo (Dub)"),
    )
    status = json.loads(invoke(workspace, "shazam", "status").stdout)["result"]
    assert status["new_since_last_import"] == 2 and status["songs"] == 5
    again = invoke(workspace, "set", "shazam:new", "--no-rekordbox")
    assert again.exit_code == 0, again.output
    fresh = json.loads(again.stdout)["result"]
    assert fresh["name"].startswith("Shazam since 2026-10-0")
    assert (fresh["songs"], fresh["owned"]) == (2, 1)
    assert fresh["rekordbox"]["status"] == "skipped" and len(fake.imported) == 1
    assert fresh["shazam"]["new"] is True

    nothing = invoke(workspace, "set", "shazam:new")
    assert nothing.exit_code == 2
    error = json.loads(nothing.stdout)["error"]
    assert error["code"] == "SHAZAM_NOTHING_NEW" and "last import" in error["message"]

    pretty = invoke(workspace, "set", "shazam", "--no-rekordbox", pretty=True)
    assert pretty.exit_code == 0, pretty.output
    assert "Shazam: Music Recognition on this Mac, 5 songs" in pretty.stdout
    assert "set shazam:new" in pretty.stdout

    # An old export: its songs are checked, but shazam:new still means this Mac's history.
    mark = shazam_history.last_import(workspace)["newest_shazamed_at"]
    export = tmp_path / "old.csv"
    export.write_text("TagTime,Title,Artist\n2019-01-01T20:00:00Z,Halo,Lumen\n", encoding="utf-8")
    old = invoke(workspace, "set", f"shazam:{export}", "--no-rekordbox", pretty=True)
    assert old.exit_code == 0, old.output
    assert "Shazam: CSV file, 1 song" in old.stdout and "shazam:new" not in old.stdout
    assert shazam_history.last_import(workspace)["newest_shazamed_at"] == mark


def test_a_failed_rekordbox_step_leaves_shazam_new_ready_to_retry(
    application, library_http, audio_factory, monkeypatch, home
):
    owned = [audio_factory("retry-0.wav", frequency=420)]
    build(library_http, owned, [("Lumen", "Halo")])
    mac_history(home, ("2026-10-01T21:14:00+00:00", "Nia Okoro", "Slow Burn"))
    fake = FakeRekordbox(monkeypatch)
    workspace = application.workspace
    first = invoke(workspace, "set", "shazam", "--no-rekordbox")
    assert first.exit_code == 0, first.output
    mac_history(
        home,
        ("2026-10-04T00:10:00+00:00", "Lumen", "Halo"),
        ("2026-10-04T00:40:00+00:00", "Someone", "Not Owned"),
    )

    def menu_disabled(playlist):
        raise AppError("APP_MENU_DISABLED", "rekordbox's Import Playlist menu is greyed out.")

    monkeypatch.setattr(rekordbox_mac, "import_playlist", menu_disabled)
    failed = invoke(workspace, "set", "shazam:new")
    assert failed.exit_code == 2
    assert json.loads(failed.stdout)["error"]["code"] == "APP_MENU_DISABLED"
    # The list was made but never finished: shazam:new still starts where it did.
    mark = shazam_history.last_import(workspace)["newest_shazamed_at"]
    assert mark == "2026-10-01T21:14:00+00:00"
    monkeypatch.setattr(rekordbox_mac, "import_playlist", fake.import_playlist)

    retry = invoke(workspace, "set", "shazam:new")
    assert retry.exit_code == 0, retry.output
    result = json.loads(retry.stdout)["result"]
    assert (result["songs"], result["owned"]) == (2, 1)
    assert result["rekordbox"]["status"] == "imported" and len(fake.imported) == 1
    # The retry finished the same list instead of making another one.
    assert library_http.get("/requests").json()["result"]["total"] == 2
    last = shazam_history.last_import(workspace)
    assert last["newest_shazamed_at"] == "2026-10-04T00:40:00+00:00"
    assert last["request_id"] == result["request_id"]

    nothing = invoke(workspace, "set", "shazam:new")
    error = json.loads(nothing.stdout)["error"]
    assert error["code"] == "SHAZAM_NOTHING_NEW"
    assert error["details"]["last_import"]["request_id"] == result["request_id"]
    assert error["details"]["last_import"]["name"] == result["name"]
    handle = result["request_id"].removeprefix("request_")[:8]
    assert f"djlib requests get {handle}" in error["message"]


def test_set_shazam_without_history_shows_the_setup_steps(application, monkeypatch):
    FakeRekordbox(monkeypatch)
    reply = invoke(application.workspace, "set", "shazam", "--no-rekordbox")
    assert reply.exit_code == 2
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "SHAZAM_HISTORY_NOT_FOUND"
    assert error["details"]["setup"] == shazam_history.SETUP
    source = invoke(application.workspace, "set", "shazam", "--source", "https://youtu.be/x")
    assert json.loads(source.stdout)["error"]["code"] == "INPUT_INVALID"
    pretty = invoke(application.workspace, "set", "shazam", pretty=True).output
    assert "No Shazam history found" in pretty
    steps = [line.strip() for line in pretty.splitlines()]
    assert any(line.startswith("1. On your iPhone: Settings") for line in steps)
    assert any(line.startswith("3. Open Control Center") for line in steps)
    assert "See what djlib finds on this Mac" in pretty and "shazam status" in pretty


def test_shazam_status_and_list(application, home, tmp_path):
    workspace = application.workspace
    empty = invoke(workspace, "shazam", "status")
    assert empty.exit_code == 0, empty.output
    result = json.loads(empty.stdout)["result"]
    assert result["ready"] is False and result["setup"] == shazam_history.SETUP
    assert [s["source"] for s in result["sources"]] == ["mac", "iphone_backup"]
    pretty = invoke(workspace, "shazam", "status", pretty=True).stdout
    assert "No Shazam history on this Mac yet" in pretty and "Turn on Shazam sync" in pretty
    assert "set shazam:apple-music" in pretty

    def next_labels(output: str) -> list[str]:
        lines = [line.strip() for line in output.splitlines()]
        steps = lines[lines.index("Next") + 1 :]
        return [line.split("  ")[0] for line in steps if line.startswith("→")]

    # Nothing comes before the first way around iCloud, so it can't start with "Or".
    assert next_labels(pretty) == [
        "→ Use Apple Music's “My Shazam Tracks” instead",
        "→ Or a Shazam CSV export",
    ]
    iphone_backup(
        home / "Library/Application Support/MobileSync/Backup/00008110",
        ("2026-09-01T12:00:00+00:00", "Lumen", "Halo"),
    )
    assert next_labels(invoke(workspace, "shazam", "status", pretty=True).stdout) == [
        "→ Read your iPhone backup instead",
        "→ Or Apple Music's “My Shazam Tracks”",
        "→ Or a Shazam CSV export",
    ]
    mac_history(
        home,
        ("2026-09-28T12:00:00+00:00", "Nia Okoro", "Slow Burn"),
        ("2026-10-03T12:00:00+00:00", "Lumen", "Halo (Extended Mix)"),
        ("2026-10-05T12:00:00+00:00", "Nia Okoro", "Slow Burn"),
    )
    result = json.loads(invoke(workspace, "shazam", "status").stdout)["result"]
    assert result["ready"] is True and result["songs"] == 2
    assert result["newest_shazamed_at"] == "2026-10-05T12:00:00+00:00"
    assert result["sources"][0]["songs"] == 2
    assert len(result["sources"][1]["backups"]) == 1
    assert result["last_import"] is None and result["setup"] == []
    pretty = invoke(workspace, "shazam", "status", pretty=True).stdout
    assert "Shazam history" in pretty and "2 songs" in pretty and "set shazam" in pretty

    listed = json.loads(invoke(workspace, "shazam", "list", "--limit", "1").stdout)["result"]
    assert listed["songs"] == 2 and listed["spec"] == "shazam"
    [newest] = listed["records"]
    assert (newest["title"], newest["count"]) == ("Halo (Extended Mix)", 1)
    since = json.loads(invoke(workspace, "shazam", "list", "--since", "2026-10-01").stdout)
    assert titles(since["result"]["records"]) == ["Slow Burn", "Halo (Extended Mix)"]
    pretty = invoke(workspace, "shazam", "list", "--limit", "1", pretty=True).stdout
    assert "Your Shazams" in pretty and "Halo (Extended Mix)" in pretty
    assert "shazam list --limit 2" in pretty
    csv = tmp_path / "export.csv"
    csv.write_text("Title,Artist\nGem Lingo,Overmono\n", encoding="utf-8")
    from_csv = json.loads(invoke(workspace, "shazam", "list", "--from", csv).stdout)["result"]
    assert titles(from_csv["records"]) == ["Gem Lingo"]
    assert from_csv["spec"] == f"shazam:{csv}"

    # Shazams without a time are the oldest you know of, so they come last.
    mixed = tmp_path / "mixed.csv"
    mixed.write_text(
        "TagTime,Title,Artist\n2026-09-01T10:00:00Z,A,X\n,C,X\n2026-10-05T10:00:00Z,B,X\n,D,X\n",
        encoding="utf-8",
    )
    listed = json.loads(invoke(workspace, "shazam", "list", "--from", mixed).stdout)["result"]
    assert titles(listed["records"]) == ["B", "A", "C", "D"]
    newest = json.loads(invoke(workspace, "shazam", "list", "--from", mixed, "--limit", "1").stdout)
    assert titles(newest["result"]["records"]) == ["B"]

    # set takes a file or a date, not both: never offer a command for a different list.
    dated = invoke(workspace, "shazam", "list", "--from", mixed, "--since", "2026-10-01")
    reply = json.loads(dated.stdout)
    assert titles(reply["result"]["records"]) == ["B"] and reply["result"]["spec"] is None
    assert "can't combine a file or Apple Music with a date" in reply["warnings"][0]
    pretty = invoke(
        workspace, "shazam", "list", "--from", mixed, "--since", "2026-10-01", pretty=True
    ).output
    assert "can't combine a file" in pretty and "set shazam" not in pretty
    mac_only = json.loads(invoke(workspace, "shazam", "list", "--since", "2026-10-01").stdout)
    assert mac_only["result"]["spec"] == "shazam:2026-10-01" and not mac_only["warnings"]


def test_doctor_reports_the_mac_shazam_history(home, tmp_path, monkeypatch):
    from djlib.native import rekordbox_mac
    from djlib.sources import runtimes

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
    monkeypatch.setattr(rekordbox_mac, "automation_allowed", lambda: True)
    monkeypatch.setattr(rekordbox_mac, "screen_locked", lambda: False)
    monkeypatch.setattr(runtimes, "javascript_runtimes", lambda: {"youtube_runtime_ready": True})
    workspace = Workspace(tmp_path / "not-yet")

    reply = invoke(workspace, "doctor")
    assert reply.exit_code == 0, reply.output  # never a required check
    result = json.loads(reply.stdout)["result"]
    assert result["shazam_history"]["found"] is False
    # The fix is a command, like every fix under a row; it shows all the setup steps.
    assert result["fixes"]["shazam"] == "djlib shazam status"
    pretty = invoke(workspace, "doctor", pretty=True).stdout.splitlines()
    at = next(n for n, line in enumerate(pretty) if "Shazam history" in line)
    assert "not set up" in pretty[at] and "optional; turn on Shazam in iCloud" in pretty[at]
    assert pretty[at + 1].strip() == "→ djlib shazam status"

    mac_db(home).close()  # Music Recognition is on but nothing has synced yet
    result = json.loads(invoke(workspace, "doctor").stdout)["result"]
    assert result["fixes"]["shazam"] == "djlib shazam status"
    pretty = invoke(workspace, "doctor", pretty=True).stdout
    row = next(line for line in pretty.splitlines() if "Shazam history" in line)
    assert "no songs yet" in row and "open Music Recognition in Control Center once" in row

    mac_history(home, ("2026-10-02T21:14:00+00:00", "Lumen", "Halo"))
    result = json.loads(invoke(workspace, "doctor").stdout)["result"]
    assert result["shazam_history"]["songs"] == 1 and "shazam" not in result["fixes"]
    pretty = invoke(workspace, "doctor", pretty=True).stdout
    row = next(line for line in pretty.splitlines() if "Shazam history" in line)
    assert "1 song, newest 2026-10-0" in row


def test_a_device_or_a_huge_file_is_not_read_as_a_shazam_history(tmp_path, monkeypatch):
    from djlib.sources import shazam_history

    for path in (Path("/dev/zero"), tmp_path):
        with pytest.raises(AppError) as refused:
            shazam_history.read_path(path)
        assert refused.value.code == "INPUT_INVALID"
    big = tmp_path / "big.csv"
    big.write_text("Title,Artist\n" + "x" * 200_000 + ",A\n", encoding="utf-8")
    with pytest.raises(AppError) as unreadable:
        shazam_history.read_path(big)
    assert unreadable.value.code == "INPUT_INVALID"
    monkeypatch.setattr(shazam_history, "CSV_BYTES", 10)
    with pytest.raises(AppError) as large:
        shazam_history.read_path(big)
    assert "too large" in large.value.message
