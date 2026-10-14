"""The songs you Shazamed, read from where macOS and the iPhone keep them.

Read automatically:

- the Mac's Music Recognition history (Control Center), which also holds the iPhone Shazam
  app's history when Shazam syncs with iCloud;
- the older Shazam app for Mac.

Read when asked: an unencrypted iPhone backup (Finder), a ShazamDataModel.sqlite file, a CSV
export, or Apple Music's "My Shazam Tracks" playlist (that one launches Music).

Databases are copied with their write-ahead log and the copy is opened read-only, so the
latest Shazams are included and the live files are never locked. Column names are read at
run time because Apple and Shazam change their Core Data models between releases.

Every source becomes the same record: title, artist, shazamed_at (ISO 8601, UTC), isrc,
shazam_key, apple_music_id, location, source and count (how often it was Shazamed).
"""

import csv
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from djlib.domain.contracts import credit_form, label_form, safe_text
from djlib.domain.errors import AppError
from djlib.workspace import atomic_json

# Tests point this at a temporary folder so they never read the real ~/Library.
HOME_ENV = "DJLIB_SHAZAM_HOME"
CORE_DATA_EPOCH = 978307200  # 2001-01-01 in Unix seconds
APPLE_MUSIC = "apple-music"
SHAZAM_PLAYLIST = "My Shazam Tracks"
STATE_FILE = "shazam.json"
# Read without being asked; shazam:new continues from these.
AUTOMATIC = frozenset({"mac", "mac_app"})

LABELS = {
    "mac": "Music Recognition on this Mac",
    "mac_app": "Shazam app for Mac",
    "iphone_backup": "iPhone backup",
    "apple_music": f"Apple Music “{SHAZAM_PLAYLIST}”",
    "csv": "CSV file",
    "sqlite": "Shazam database file",
}
# Apple's steps on iOS 26 and macOS 26 (support.apple.com/118570, the Shazam guide): the
# Shazam app syncs only with its own iCloud Sync switch on, and macOS 26 keeps Recognize
# Music out of the menu bar until it is added.
SETUP = [
    "On your iPhone, open Shazam → Settings → turn on iCloud Sync. (If it's off or greyed out: "
    "Settings → [your name] → iCloud → See All → turn on Shazam and Music Recognition.)",
    "On this Mac: System Settings → [your name] → iCloud → See All → turn on Music Recognition.",
    "Add Recognize Music to the menu bar and click it once (macOS 26: Control Center → Edit "
    "Controls → Recognize Music; macOS 14–15: System Settings → Control Center → Music "
    "Recognition → Show in Menu Bar). Your iPhone's Shazams arrive within a few minutes.",
]
ALTERNATIVES = [
    "djlib set shazam:apple-music (if Shazam saves to Apple Music's “My Shazam Tracks”)",
    "djlib set shazam:BACKUP_OR_CSV (an iPhone backup folder, or a CSV from shazam.com/myshazam: "
    "Download CSV, if your account shows it)",
]
# Full Disk Access belongs to the app djlib runs in, which isn't always a terminal.
FULL_DISK_APPS = "the app you run djlib in (Terminal, iTerm, VS Code or Claude)"
FULL_DISK_ACCESS = (
    f"macOS keeps your Shazam history private. Allow {FULL_DISK_APPS} under System Settings → "
    "Privacy & Security → Full Disk Access, quit and reopen it, then try again."
)
FULL_DISK_SETTINGS = (
    'open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"'
)
SETUP_COMMAND = "djlib shazam status"  # shows SETUP when there is no history


def library() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home()) / "Library"


def mac_history() -> Path:
    return library() / "Application Support" / "com.apple.shazamd" / "ShazamLibrary.sqlite"


def mac_app_databases() -> list[Path]:
    """The older Shazam app for Mac keeps its library in a group container."""
    found = []
    for container in sorted((library() / "Group Containers").glob("*com.shazam*")):
        path = container / "com.shazam.mac.Shazam" / "ShazamDataModel.sqlite"
        if exists(path):
            found.append(path)
    return found


def backups_folder() -> Path:
    return library() / "Application Support" / "MobileSync" / "Backup"


def exists(path: Path) -> bool | None:
    """Whether the file is there; None when macOS won't say (no Full Disk Access)."""
    try:
        path.stat()
        return True
    except PermissionError:
        return None
    except OSError:
        return False


def no_access(path: Path) -> AppError:
    """SHAZAM_ACCESS_DENIED, with the command that opens Full Disk Access as the next step."""
    return AppError(
        "SHAZAM_ACCESS_DENIED",
        FULL_DISK_ACCESS,
        details={
            "path": str(path),
            "next": FULL_DISK_SETTINGS,
            "next_label": "Open Full Disk Access",
        },
    )


def setup_message(start: str) -> str:
    """``start``, then the setup steps and the alternatives, one per line."""
    lines = [f"{start} To bring your Shazams here:"]
    lines += [f"{number}. {step}" for number, step in enumerate(SETUP, 1)]
    lines += [f"Or run {alternative}." for alternative in ALTERNATIVES]
    return "\n".join(lines)


def not_found(start: str = "No Shazam history was found on this Mac.") -> AppError:
    """The steps to turn on Shazam sync are in ``details`` (a terminal shows them under the
    message), so the message stays one sentence."""
    return AppError(
        "SHAZAM_HISTORY_NOT_FOUND",
        start,
        details={
            "setup": SETUP,
            "alternatives": ALTERNATIVES,
            "steps": setup_message(start),
            "next": SETUP_COMMAND,
            "next_label": "When they've synced, check",
        },
    )


# -- reading databases -------------------------------------------------------------------------


def stamp(path: Path) -> tuple:
    try:
        stat = path.stat()
    except OSError:
        return ()
    return stat.st_mtime_ns, stat.st_size


@contextmanager
def opened(path: Path, wal: Path | None = None):
    """A read-only connection to a private copy of the database and its write-ahead log.

    The -shm index is left behind: SQLite rebuilds it from the log, while a copied index
    could describe a log that changed during the copy.
    """
    wal = wal if wal is not None else Path(f"{path}-wal")
    with tempfile.TemporaryDirectory(prefix="djlib-shazam-") as folder:
        copy = Path(folder) / "history.sqlite"
        try:
            for _ in range(3):  # copy again if Shazam wrote while we copied
                before = (stamp(path), stamp(wal))
                shutil.copyfile(path, copy)
                Path(f"{copy}-wal").unlink(missing_ok=True)
                if exists(wal):
                    shutil.copyfile(wal, Path(f"{copy}-wal"))
                if (stamp(path), stamp(wal)) == before:
                    break
        except PermissionError as exc:
            raise no_access(path) from exc
        except FileNotFoundError as exc:
            raise AppError("FILE_UNAVAILABLE", f"Can't find {path}.") from exc
        connection = sqlite3.connect(f"{copy.as_uri()}?mode=ro", uri=True)
        try:
            yield connection
        except sqlite3.DatabaseError as exc:
            raise AppError(
                "SHAZAM_HISTORY_UNREADABLE",
                f"{path.name} isn't a Shazam library djlib can read ({exc}).",
                details={"path": str(path)},
            ) from exc
        finally:
            connection.close()


SYSTEM_COLUMNS = frozenset({"Z_PK", "Z_ENT", "Z_OPT"})
# Candidate Core Data columns, most specific first.
TITLE = ("ZTITLE", "ZTRACKTITLE", "ZTRACKNAME", "ZSONGTITLE", "ZSONGNAME", "ZNAME")
ARTIST = ("ZARTIST", "ZARTISTNAME", "ZSUBTITLE", "ZARTISTS", "ZARTISTSNAME")
ARTIST_TABLE = ("ZNAME", "ZARTISTNAME", "ZARTIST", "ZTITLE")
DATE = ("ZDATE", "ZTAGDATE", "ZTIMESTAMP", "ZCREATIONDATE", "ZDATECREATED", "ZCREATEDDATE")
ISRC = ("ZISRC",)
SHAZAM_KEY = ("ZSHAZAMKEY", "ZTRACKKEY", "ZKEY", "ZSHAZAMID")
APPLE_MUSIC_ID = ("ZAPPLEMUSICID", "ZAPPLEMUSICTRACKID", "ZADAMID", "ZTRACKADAMID", "ZSTOREID")
PLACE = ("ZLOCATIONNAME", "ZPLACENAME", "ZCITY", "ZLOCALITY", "ZLOCATION")
PLACE_TABLE = ("ZNAME", *PLACE)
LATITUDE = ("ZLATITUDE", "ZLAT")
LONGITUDE = ("ZLONGITUDE", "ZLON", "ZLNG")
# A tag is one Shazam; prefer those tables over track tables, which may hold a song once.
EVENT_TABLES = ("ZSHTAGRESULTMO", "ZTAGMO", "ZSHTAGMO", "ZSHTRACKMO", "ZTRACKMO")


def kind(table: str) -> str:
    """What a linked table describes: the song itself, its artists, where, or something else."""
    if "ARTIST" in table:
        return "artist"
    if any(word in table for word in ("LOCATION", "GEO", "PLACE", "VENUE")):
        return "place"
    if any(word in table for word in ("GENRE", "ALBUM", "LABEL", "IMAGE", "ARTWORK", "LINK")):
        return "other"
    return "track"


def schema(connection) -> dict[str, tuple[str, list[str]]]:
    """Upper-case table name → (actual name, upper-case column names)."""
    names = [
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    ]
    return {
        name.upper(): (
            name,
            [column[1].upper() for column in connection.execute(f'PRAGMA table_info("{name}")')],
        )
        for name in names
    }


def link_target(column: str, tables: dict, own: str) -> str | None:
    """The table a Core Data to-one column such as ZTRACK points at (ZTRACKMO or ZSHTRACKMO)."""
    if column in SYSTEM_COLUMNS or not column.startswith("Z"):
        return None
    for name in (f"{column}MO", f"ZSH{column[1:]}MO"):
        if name in tables and name != own:
            return name
    return None


def inverse_column(table: str) -> str | None:
    """The column other tables use to point at ``table``: ZSHTAGRESULTMO ← ZTAGRESULT."""
    if not table.endswith("MO"):
        return None
    stem = table[3:-2] if table.startswith("ZSH") else table[1:-2]
    return f"Z{stem}" if stem else None


def event_table(tables: dict) -> str | None:
    def dated(name):
        return any(column in DATE for column in tables[name][1])

    def titled(name):
        columns = tables[name][1]
        if any(column in TITLE for column in columns):
            return True
        if any(
            (target := link_target(column, tables, name)) and kind(target) == "track"
            for column in columns
        ):
            return True
        inverse = inverse_column(name)
        return any(
            inverse in other_columns and kind(other) == "track"
            for other, (_, other_columns) in tables.items()
            if other != name
        )

    for name in EVENT_TABLES:
        if name in tables and dated(name) and titled(name):
            return name
    others = [n for n in tables if kind(n) == "track" and dated(n) and titled(n)]
    return min(others, key=lambda n: ("TAG" not in n, "TRACK" not in n, n), default=None)


def rows(connection, tables: dict, table: str) -> list[dict]:
    cursor = connection.execute(f'SELECT * FROM "{tables[table][0]}"')
    keys = [column[0].upper() for column in cursor.description]
    return [dict(zip(keys, row, strict=True)) for row in cursor]


def text(value) -> str:
    return " ".join(safe_text(value).split()) if isinstance(value, str) else ""


def identifier(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        value = int(value) if value.is_integer() else value
    value = str(value).strip()
    return value or None


def first(views: list[tuple[str, dict]], columns: tuple, kinds=("track",), read=text):
    for table, row in views:
        if kind(table) not in kinds:
            continue
        for column in columns:
            value = read(row.get(column))
            if value:
                return value
    return None


def number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def place(views: list[tuple[str, dict]]) -> str | None:
    named = first(views, PLACE_TABLE, kinds=("place",)) or first(views, PLACE)
    if named:
        return named
    for kinds in (("place",), ("track",)):
        latitude = first(views, LATITUDE, kinds=kinds, read=number)
        longitude = first(views, LONGITUDE, kinds=kinds, read=number)
        if latitude is not None and longitude is not None and (latitude or longitude):
            return f"{latitude:.4f}, {longitude:.4f}"
    return None


COORDINATES = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")


def place_name(location: str | None) -> str:
    """A place's name, or "" for bare coordinates, which mean nothing to a reader."""
    return "" if not location or COORDINATES.match(location) else location


def read_database(connection, source: str) -> list[dict]:
    """Shazam records from any of the known Shazam Core Data layouts."""
    tables = schema(connection)
    main = event_table(tables)
    if main is None:
        return []
    forward = {
        column: target
        for column in tables[main][1]
        if (target := link_target(column, tables, main))
    }
    linked = {
        target: {row.get("Z_PK"): row for row in rows(connection, tables, target)}
        for target in set(forward.values())
    }
    inverse = inverse_column(main)
    backward: dict[str, dict] = {}
    for table, (_, columns) in tables.items():
        if table != main and inverse in columns:
            grouped: dict = {}
            for row in rows(connection, tables, table):
                grouped.setdefault(row.get(inverse), []).append(row)
            backward[table] = grouped
    records = []
    for row in rows(connection, tables, main):
        related = [
            (target, [linked[target][row[column]]])
            for column, target in forward.items()
            if row.get(column) in linked[target]
        ]
        related += [
            (table, grouped.get(row.get("Z_PK")) or []) for table, grouped in backward.items()
        ]
        # The Shazam's own row first, then its song (ZTRACKMO) before other linked rows.
        related.sort(key=lambda pair: not any(word in pair[0] for word in ("TRACK", "SONG")))
        views = [(main, row), *((table, found[0]) for table, found in related if found)]
        title = first(views, TITLE)
        if not title:
            continue
        artist = first(views, ARTIST)
        if not artist:
            # Older Shazam apps keep one row per credited artist.
            names = (
                first([(table, item)], ARTIST_TABLE, kinds=("artist",))
                for table, found in related
                for item in found
            )
            artist = ", ".join(dict.fromkeys(name for name in names if name))
        records.append(
            record(
                title,
                artist,
                when(first(views, DATE, read=lambda value: value)),
                source,
                isrc=first(views, ISRC),
                shazam_key=first(views, SHAZAM_KEY, read=identifier),
                apple_music_id=first(views, APPLE_MUSIC_ID, read=identifier),
                location=place(views),
            )
        )
    return records


def record(title, artist, shazamed_at, source, **extra) -> dict:
    return {
        "title": text(title),
        "artist": text(artist) or None,
        "shazamed_at": shazamed_at,
        "isrc": (text(extra.get("isrc")) or "").upper() or None,
        "shazam_key": extra.get("shazam_key"),
        "apple_music_id": extra.get("apple_music_id"),
        "location": extra.get("location"),
        "source": source,
        "count": 1,
        "last_shazamed_at": shazamed_at,
    }


def when(value) -> str | None:
    """ISO 8601 UTC from Core Data seconds (since 2001), Unix seconds or ms, or a date string.

    A date without a time zone is taken as this Mac's local time.
    """
    moment = None
    if isinstance(value, str) and value.strip():
        stripped = value.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
            return when(float(stripped))
        try:
            moment = datetime.fromisoformat(stripped)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.astimezone()
    elif isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        seconds = float(value)
        if seconds > 1e11:
            seconds /= 1000
        elif seconds < 1.2e9:  # Unix 1.2e9 is 2008; Core Data reaches it only in 2039
            seconds += CORE_DATA_EPOCH
        try:
            moment = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def read_sqlite(path: Path, source: str, wal: Path | None = None) -> list[dict]:
    with opened(path, wal) as connection:
        return read_database(connection, source)


# -- iPhone backups ----------------------------------------------------------------------------


def backups() -> list[Path]:
    """Finder backups of an iPhone or iPad on this Mac, newest first."""
    folder = backups_folder()
    try:
        found = [entry for entry in folder.iterdir() if (entry / "Manifest.db").is_file()]
    except PermissionError as exc:
        raise no_access(folder) from exc
    except OSError:
        return []
    return sorted(found, key=lambda entry: stamp(entry / "Manifest.db"), reverse=True)


def read_backup(folder: Path) -> list[dict]:
    """The Shazam app's library inside an unencrypted iPhone backup."""
    manifest = folder / "Manifest.db"
    encrypted = AppError(
        "SHAZAM_BACKUP_ENCRYPTED",
        f"The iPhone backup in {folder} is encrypted, so djlib can't read Shazam from it. "
        "Turn on Shazam in iCloud instead (djlib shazam status shows how), or make an "
        "unencrypted backup in Finder.",
    )
    try:
        with (folder / "Manifest.plist").open("rb") as stream:
            if plistlib.load(stream).get("IsEncrypted"):
                raise encrypted
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    try:
        with opened(manifest) as connection:
            files = connection.execute(
                "SELECT fileID, relativePath FROM Files "
                "WHERE relativePath LIKE '%ShazamDataModel.sqlite%'"
            ).fetchall()
    except AppError as error:
        # An encrypted backup's Manifest.db doesn't open as SQLite at all.
        if error.code == "SHAZAM_HISTORY_UNREADABLE" and "not a database" in error.message:
            raise encrypted from error
        raise
    stored = {path: folder / file_id[:2] / file_id for file_id, path in files}
    records = []
    for path, file in stored.items():
        if path.endswith("ShazamDataModel.sqlite") and exists(file):
            wal = stored.get(f"{path}-wal") or Path(f"{file}-wal")
            records += read_sqlite(file, "iphone_backup", wal)
    if not stored:
        raise AppError(
            "SHAZAM_HISTORY_NOT_FOUND",
            f"The iPhone backup in {folder} has no Shazam app library. Is the Shazam app "
            "installed on that iPhone?",
        )
    return records


# -- CSV ---------------------------------------------------------------------------------------


def header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


CSV_COLUMNS = {
    "title": ("title", "track", "trackname", "tracktitle", "song", "songname", "name"),
    "artist": ("artist", "artists", "artistname", "subtitle"),
    "date": (
        "tagtime",
        "date",
        "datetime",
        "time",
        "shazamed",
        "shazamedat",
        "dateadded",
        "timestamp",
        "tagdate",
    ),
    "isrc": ("isrc",),
    "shazam_key": ("trackkey", "shazamkey", "shazamid"),
    "apple_music_id": ("applemusicid", "adamid"),
    "location": ("location", "place", "city"),
}


def csv_header(table: list[list[str]]) -> tuple[int, dict | None]:
    """(row number, field → column) of the first row naming a title and an artist column."""
    for index, cells in enumerate(table[:20]):
        keys = [header_key(cell) for cell in cells]
        columns = {
            field: next((keys.index(name) for name in names if name in keys), None)
            for field, names in CSV_COLUMNS.items()
        }
        if columns["title"] is not None and columns["artist"] is not None:
            return index, columns
    return 0, None


# Columns only Shazam's own export has; a DJ's spreadsheet has Artist and Title too.
SHAZAM_ONLY = frozenset({"tagtime", "trackkey", "shazamkey", "shazamid"})
# A spreadsheet's Mix column keeps the version exact.
VERSION_COLUMNS = ("version", "mix", "remix", "mixname", "versionmix")


def rows_of(text: str) -> list[list[str]]:
    """A CSV, TSV or copied spreadsheet's rows, its delimiter sniffed from the first lines."""
    lines = text.splitlines()
    head = "\n".join(lines[:20])
    try:
        dialect = csv.Sniffer().sniff(head, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in head else csv.excel
    try:
        return list(csv.reader(lines, dialect))
    except csv.Error:
        return []


def csv_kind(path: Path) -> str | None:
    """What `djlib set FILE` makes of a .csv or .tsv file: "shazam" for Shazam's export (a
    "Shazam Library" line above the header, or its TagTime or TrackKey column), "songs" for
    any other file with Title and Artist columns, else None (a tracklist)."""
    if path.suffix.lower() not in {".csv", ".tsv"}:
        return None
    try:
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            head = stream.read(64_000).decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    lines = head.splitlines()[:20]
    if lines and header_key(lines[0]) == "shazamlibrary":
        return "shazam"
    table = rows_of("\n".join(lines))
    index, columns = csv_header(table)
    if columns is None:
        return None
    return "shazam" if SHAZAM_ONLY & {header_key(cell) for cell in table[index]} else "songs"


def song_rows(text: str) -> list[dict] | None:
    """{artist, title, version} for each row under an Artist and Title header, in order (a
    Mix or Version column gives the version); None when there is no such header."""
    table = rows_of(text)
    index, columns = csv_header(table)
    if columns is None:
        return None
    keys = [header_key(cell) for cell in table[index]]
    version = next((keys.index(name) for name in VERSION_COLUMNS if name in keys), None)

    def cell(cells: list[str], column: int | None) -> str:
        if column is None or column >= len(cells):
            return ""
        return " ".join(cells[column].split())

    rows = []
    for cells in table[index + 1 :]:
        row = {
            "artist": cell(cells, columns["artist"]),
            "title": cell(cells, columns["title"]),
            "version": cell(cells, version),
        }
        if row["artist"] or row["title"]:
            rows.append(row)
    return rows


def read_csv(path: Path) -> list[dict]:
    """The old shazam.com export ("Index,TagTime,Title,Artist,URL,TrackKey") or any CSV with
    title and artist columns; a "Shazam Library" line above the header is skipped."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise AppError("INPUT_INVALID", f"Could not read {path} as a UTF-8 CSV file.") from exc
    lines = content.splitlines()
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    try:
        table = list(csv.reader(lines, dialect))
    except csv.Error as exc:
        raise AppError("INPUT_INVALID", f"{path.name} isn't a CSV djlib can read ({exc}).") from exc
    index, columns = csv_header(table)
    if columns is None:
        raise AppError("INPUT_INVALID", f"{path.name} has no Title and Artist columns.")

    def cell(cells, field):
        column = columns[field]
        return cells[column].strip() if column is not None and column < len(cells) else ""

    records = []
    for cells in table[index + 1 :]:
        if not cell(cells, "title"):
            continue
        records.append(
            record(
                cell(cells, "title"),
                cell(cells, "artist"),
                when(cell(cells, "date")),
                "csv",
                isrc=cell(cells, "isrc"),
                shazam_key=cell(cells, "shazam_key") or None,
                apple_music_id=cell(cells, "apple_music_id") or None,
                location=cell(cells, "location") or None,
            )
        )
    return records


# -- Apple Music -------------------------------------------------------------------------------

MUSIC_SCRIPT = f"""
const list = Application('Music').playlists.byName({json.dumps(SHAZAM_PLAYLIST)});
const names = list.tracks.name(), artists = list.tracks.artist(), added = list.tracks.dateAdded();
JSON.stringify(names.map((name, i) => ({{
  name: name, artist: artists[i], added: added[i] ? added[i].toISOString() : null
}})));
"""


def run_music_script(script: str) -> subprocess.CompletedProcess:
    """Run JavaScript for Automation; tests replace this so Music is never driven."""
    return subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=180,
    )


def read_apple_music() -> list[dict]:
    """Apple Music's “My Shazam Tracks” playlist, which Shazam fills when it is connected."""
    if sys.platform != "darwin":
        raise AppError("SHAZAM_MUSIC_UNSUPPORTED", "Apple Music can only be read on a Mac.")
    try:
        done = run_music_script(MUSIC_SCRIPT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "SHAZAM_MUSIC_FAILED", "Music didn't answer; try again.", retryable=True
        ) from exc
    if done.returncode != 0:
        error = done.stderr or ""
        if "-1743" in error or "Not authorized" in error or "not allowed" in error:
            raise AppError(
                "SHAZAM_MUSIC_NOT_ALLOWED",
                "Allow your terminal app to control Music under System Settings → Privacy & "
                "Security → Automation, then try again.",
            )
        if "-1728" in error or "Can't get" in error or "Can’t get" in error:
            raise AppError(
                "SHAZAM_PLAYLIST_NOT_FOUND",
                f"Music has no “{SHAZAM_PLAYLIST}” playlist. In the Shazam app on your iPhone, "
                "open Settings and connect Apple Music so it saves your Shazams there, then "
                "wait for the playlist to appear in Music on this Mac.",
            )
        raise AppError("SHAZAM_MUSIC_FAILED", "Music didn't answer; try again.", retryable=True)
    try:
        tracks = json.loads(done.stdout or "[]")
    except ValueError as exc:
        raise AppError("SHAZAM_MUSIC_FAILED", "Music's answer couldn't be read.") from exc
    return [
        record(track.get("name"), track.get("artist"), when(track.get("added")), "apple_music")
        for track in tracks
        if isinstance(track, dict) and text(track.get("name"))
    ]


# -- merging and filtering ---------------------------------------------------------------------


def merge(records: list[dict]) -> list[dict]:
    """One record per song, oldest Shazam first (the order you heard them).

    The same song is matched by ISRC, else by equivalent artist and title; it keeps its first
    Shazam time, its latest (``last_shazamed_at``) and how often you Shazamed it. Songs without
    a time keep their order at the end.
    """
    ordered = sorted(
        enumerate(records),
        key=lambda pair: (pair[1]["shazamed_at"] is None, pair[1]["shazamed_at"] or "", pair[0]),
    )
    merged: list[dict] = []
    seen: dict = {}
    for _, item in ordered:
        keys = song_keys(item)
        found = next((seen[key] for key in keys if key in seen), None)
        if found is None:
            found = {**item, "count": 0}
            merged.append(found)
        else:
            for field in ("artist", "isrc", "shazam_key", "apple_music_id", "location"):
                found[field] = found[field] or item[field]
        found["count"] += item.get("count") or 1
        latest = (found.get("last_shazamed_at"), item.get("last_shazamed_at"), item["shazamed_at"])
        found["last_shazamed_at"] = max(filter(None, latest), default=None)
        for key in keys:
            seen.setdefault(key, found)
    return merged


def song_keys(item: dict) -> list[tuple]:
    """What makes two Shazams the same song: the ISRC, or equivalent artist and title."""
    labels = (item["artist"] or "", item["title"])
    keys = [("label", label_form(*labels)), ("credit", credit_form(*labels))]
    if item["isrc"]:
        keys.insert(0, ("isrc", item["isrc"].replace("-", "")))
    return keys


def after(records: list[dict], moment: datetime, *, inclusive: bool = True) -> list[dict]:
    """Records Shazamed at or after ``moment`` (strictly after unless ``inclusive``)."""
    kept = []
    for item in records:
        if not item["shazamed_at"]:
            continue
        at = datetime.fromisoformat(item["shazamed_at"])
        if at > moment or (inclusive and at == moment):
            kept.append(item)
    return kept


def unseen(records: list[dict], previous: dict | None) -> list[dict]:
    """``records`` without the repeat Shazams that a list cut at 1000 songs already checked.

    Such a list (an entry of ``held`` in the last import) ends at the first Shazam of its last
    song (``through``), so repeats of its songs can come after that. The ones already in the
    history when the list was made (up to ``until``) were checked with it and are left out;
    a song Shazamed again later is new again.
    """
    kept = records
    for cut in (previous or {}).get("held") or []:
        if not (isinstance(cut, dict) and cut.get("through") and cut.get("until")):
            continue
        through = datetime.fromisoformat(cut["through"])
        until = datetime.fromisoformat(cut["until"])
        since = cut.get("since")
        window = (
            after(kept, datetime.fromisoformat(since), inclusive=cut.get("inclusive", True))
            if since
            else kept
        )
        listed = {
            key
            for item in window
            if item["shazamed_at"] and datetime.fromisoformat(item["shazamed_at"]) <= through
            for key in song_keys(item)
        }
        kept = [
            item
            for item in kept
            if not (
                item["shazamed_at"]
                and through < datetime.fromisoformat(item["shazamed_at"]) <= until
                and listed.intersection(song_keys(item))
            )
        ]
    return kept


def local_time(value: str | None) -> str:
    """ "2026-10-02 21:14" in this Mac's time zone; "2026-10-02" for a date without a time
    (CSV exports and dates you typed are midnight here, which isn't when you Shazamed)."""
    if not value:
        return ""
    moment = datetime.fromisoformat(value).astimezone()
    return moment.strftime("%Y-%m-%d" if midnight(moment) else "%Y-%m-%d %H:%M")


def midnight(moment: datetime) -> bool:
    return (moment.hour, moment.minute, moment.second, moment.microsecond) == (0, 0, 0, 0)


def today() -> date:
    """This Mac's date (tests freeze it)."""
    return datetime.now().astimezone().date()


def day(value: str, *, year: bool | None = None) -> str:
    """ "12 Sep" in this Mac's time zone, with the year when it isn't this year's."""
    moment = datetime.fromisoformat(value).astimezone()
    if year is None:
        year = moment.year != today().year
    return f"{moment.day} {moment:%b}" + (f" {moment.year}" if year else "")


def day_time(value: str) -> str:
    """ "Sat 10 Oct 22:44"; "Thu 1 Oct" for a date without a time."""
    moment = datetime.fromisoformat(value).astimezone()
    text = f"{moment:%a} {day(value)}"
    return text if midnight(moment) else f"{text} {moment:%H:%M}"


def day_range(first: str, last: str) -> str:
    """ "12 Sep – 12 Oct", "12 Oct", or "2 Mar 2019 – 20 Nov 2023" across years."""
    start, end = (datetime.fromisoformat(value).astimezone() for value in (first, last))
    year = start.year != end.year or end.year != today().year
    if start.date() == end.date():
        return day(first, year=year)
    return f"{day(first, year=year)} – {day(last, year=year)}"


RECENT_DAYS = 30


def recent_since(newest: str | None) -> tuple[str, bool]:
    """(YYYY-MM-DD, ends today) for a first look at a long history: the last RECENT_DAYS days,
    or the RECENT_DAYS before the newest Shazam when nothing is that recent."""
    end = today()
    last = datetime.fromisoformat(newest).astimezone().date() if newest else end
    if last < end - timedelta(days=RECENT_DAYS):
        end = last
    return f"{end - timedelta(days=RECENT_DAYS):%Y-%m-%d}", end == today()


def set_name(sources: list[dict], first: str | None, last: str | None) -> str:
    """ "Shazam 12 Sep – 12 Oct", or after a file: "Shazam (shazamlibrary.csv)".

    No ":" in names: rekordbox names a playlist after its file.
    """
    kinds = [source.get("source") for source in sources]
    if kinds == ["iphone_backup"]:
        return "Shazam (iPhone backup)"
    if kinds and kinds[0] in {"csv", "sqlite"} and sources[0].get("path"):
        return f"Shazam ({Path(sources[0]['path']).name.replace(':', ' ')})"
    if first and last:
        return f"Shazam {day_range(first, last)}"
    return "Shazam"


# -- remembering imports -----------------------------------------------------------------------


def state_file(workspace) -> Path:
    return workspace.root / STATE_FILE


def last_import(workspace) -> dict | None:
    if workspace is None:
        return None
    try:
        data = json.loads(state_file(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = data.get("last_import") if isinstance(data, dict) else None
    return found if isinstance(found, dict) else None


def from_this_mac(summary: dict) -> bool:
    """Whether an import came from this Mac's own history, the one shazam:new reads."""
    sources = [source.get("source") for source in summary.get("sources") or []]
    return bool(sources) and all(source in AUTOMATIC for source in sources)


def remember_import(
    workspace, summary: dict, request_id: str | None = None, collection_id: str | None = None
) -> dict | None:
    """Keep the newest Shazam time imported from this Mac's history, so shazam:new starts
    after it. It never moves back, and imports from a file or Apple Music leave it alone
    (None). Lists cut at 1000 songs are kept in ``held`` (see ``unseen``), and the rest of
    one that shazam:new can't bring in ``pending``. The list holding the newest Shazams is
    kept in ``newest_list``, so "nothing new" points to its crate, not an older list's."""
    if not from_this_mac(summary):
        return None
    previous = last_import(workspace) or {}
    marks = (previous.get("newest_shazamed_at"), summary.get("newest_shazamed_at"))
    mark = max(filter(None, marks), key=datetime.fromisoformat, default=None)
    this = {"name": summary.get("name"), "request_id": request_id, "collection_id": collection_id}
    newest_list = this
    # An older list (the whole history after the last 30 days) leaves the mark where it was.
    if previous and summary.get("newest_shazamed_at") != mark:
        newest_list = previous.get("newest_list") or {key: previous.get(key) for key in this}
    entry = {
        "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "newest_shazamed_at": mark,
        "songs": summary.get("songs"),
        **this,
        "sources": [source.get("source") for source in summary.get("sources") or []],
        "held": held(previous, summary, mark),
        "newest_list": newest_list,
        "pending": pending(previous, summary),
    }
    atomic_json(state_file(workspace), {"last_import": entry})
    return entry


def pending(previous: dict, summary: dict) -> dict | None:
    """{spec, songs, since}: the list that brings the rest of a list cut at 1000 songs when
    shazam:new can't (its ``next_spec`` is a date), so status keeps offering it. It stays
    until an import reads from that day or earlier (the whole history, or an earlier date).
    """
    kept = previous.get("pending")
    if not (isinstance(kept, dict) and kept.get("since")):
        kept = None
    elif not summary.get("new"):
        start = summary.get("since")
        if not start or datetime.fromisoformat(start) <= datetime.fromisoformat(kept["since"]):
            kept = None
    spec = summary.get("next_spec") or ""
    if summary.get("remaining") and spec.startswith("shazam:") and spec != "shazam:new":
        moment, _, _ = since_moment(spec.removeprefix("shazam:"), None)
        since = moment.astimezone(UTC).isoformat(timespec="seconds") if moment else None
        kept = {"spec": spec, "songs": summary["remaining"], "since": since}
    return kept


def held(previous: dict, summary: dict, mark: str | None) -> list[dict]:
    """The cut lists shazam:new keeps skipping repeats for, after this import.

    A list cut at 1000 songs that set the mark joins the cut lists shazam:new continued
    through, or replaces them when it read the history from the start or a date. All are
    dropped once the mark reaches the newest Shazam any of them was made with.
    """
    cuts = [cut for cut in previous.get("held") or [] if isinstance(cut, dict) and cut.get("until")]
    through = summary.get("newest_shazamed_at")
    if summary.get("remaining") and summary.get("newest_in_history") and through == mark:
        cut = {
            "since": summary.get("since"),
            "inclusive": not summary.get("new"),
            "through": through,
            "until": summary["newest_in_history"],
        }
        cuts = [*cuts, cut] if summary.get("new") else [cut]
    if mark and all(
        datetime.fromisoformat(cut["until"]) <= datetime.fromisoformat(mark) for cut in cuts
    ):
        return []
    return cuts


# -- loading -----------------------------------------------------------------------------------


def looks_like_path(value: str) -> bool:
    """Written as a path: ``./new``, ``~/x``, ``a/b`` or ``history.csv``, never just ``new``."""
    return "/" in value or value.startswith(("~", ".")) or bool(Path(value).suffix)


def is_date(value: str) -> bool:
    """``2026-10-01`` or a date and time; ``2026-10-01.csv`` is a file name."""
    named = re.search(r"\.[a-z]\w*$", value, re.IGNORECASE)
    return bool(re.match(r"\d{4}-\d{2}-\d{2}", value)) and not named


def parse_spec(value: str) -> tuple[str | None, str | None]:
    """``shazam[:SPEC]`` → (source, since).

    SPEC is an existing path (an iPhone backup folder, a .sqlite or a .csv), ``apple-music``,
    ``new`` (since the last import) or a date such as ``2026-10-01``. The words and dates win
    over a file or folder of the same name here; ``./new`` means that folder.
    """
    head, _, rest = value.partition(":")
    if head.strip().lower() != "shazam":
        raise AppError("INPUT_INVALID", "Use shazam, shazam:new, shazam:DATE or shazam:FILE.")
    rest = rest.strip()
    if not rest:
        return None, None
    found = exists(Path(rest).expanduser()) is not False
    if found and looks_like_path(rest):
        return rest, None
    if rest.lower() in {APPLE_MUSIC, "applemusic", "music"}:
        return APPLE_MUSIC, None
    if rest.lower() == "new" or is_date(rest):
        return None, rest
    if found:
        return rest, None
    if re.fullmatch(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", rest):
        raise AppError(
            "INPUT_INVALID", f"Write the date {rest} as YYYY-MM-DD, e.g. shazam:2026-10-01."
        )
    if looks_like_path(rest):
        raise AppError("FILE_UNAVAILABLE", f"Can't find {rest}. Check the path.")
    raise AppError(
        "INPUT_INVALID",
        "After shazam: give a date (shazam:2026-10-01), new, apple-music, or the path to an "
        "iPhone backup folder, a ShazamDataModel.sqlite or a CSV file.",
    )


def since_moment(since: str | None, workspace) -> tuple[datetime | None, bool, dict | None]:
    """(moment, inclusive, last import) for ``since``: None, ``new`` or a date."""
    if not since:
        return None, True, None
    if since.strip().lower() == "new":
        previous = last_import(workspace)
        newest = (previous or {}).get("newest_shazamed_at")
        return (datetime.fromisoformat(newest) if newest else None), False, previous
    try:
        moment = datetime.fromisoformat(since.strip())
    except ValueError as exc:
        raise AppError(
            "INPUT_INVALID", f"“{since}” isn't a date; use YYYY-MM-DD, e.g. 2026-10-01."
        ) from exc
    return (moment if moment.tzinfo else moment.astimezone()), True, None


def read_path(path: Path) -> tuple[str, list[dict]]:
    """(source, records) from a backup folder, the Backup folder itself, a database or a CSV."""
    if path.is_dir():
        if (path / "Manifest.db").is_file():
            return "iphone_backup", read_backup(path)
        for name in ("ShazamLibrary.sqlite", "ShazamDataModel.sqlite"):
            if (path / name).is_file():
                return "sqlite", read_sqlite(path / name, "sqlite")
        inside = sorted(
            (entry for entry in path.iterdir() if (entry / "Manifest.db").is_file()),
            key=lambda entry: stamp(entry / "Manifest.db"),
            reverse=True,
        )
        for backup in inside:
            try:
                return "iphone_backup", read_backup(backup)
            except AppError as error:
                if error.code not in {"SHAZAM_HISTORY_NOT_FOUND", "SHAZAM_BACKUP_ENCRYPTED"}:
                    raise
        if inside:
            raise AppError(
                "SHAZAM_HISTORY_NOT_FOUND", f"None of the iPhone backups in {path} has Shazam."
            )
        raise AppError(
            "INPUT_INVALID",
            f"{path} is not an iPhone backup folder (it has no Manifest.db).",
        )
    # A device (shazam:/dev/zero) or a huge file would be read until the disk or memory ran
    # out.
    try:
        regular, size = path.is_file(), path.stat().st_size
    except OSError as exc:
        raise AppError("FILE_UNAVAILABLE", f"Can't read {path}.") from exc
    csv_file = path.suffix.lower() in {".csv", ".txt", ".tsv"}
    limit = CSV_BYTES if csv_file else DATABASE_BYTES
    if not regular:
        raise AppError("INPUT_INVALID", f"{path} is not a Shazam CSV, database or backup folder.")
    if size > limit:
        raise AppError(
            "INPUT_INVALID",
            f"{path.name} is over {limit // 1_000_000} MB, too large for a Shazam history.",
        )
    if csv_file:
        return "csv", read_csv(path)
    return "sqlite", read_sqlite(path, "sqlite")


CSV_BYTES = 50_000_000
DATABASE_BYTES = 1_000_000_000


def automatic_sources() -> list[tuple[str, Path]]:
    """The sources in AUTOMATIC, with their paths."""
    return [("mac", mac_history()), *(("mac_app", path) for path in mac_app_databases())]


def read_automatic() -> tuple[list[dict], list[dict], list[dict]]:
    """(records, sources read, problems) from the Mac history and the older Mac app."""
    records, sources, problems = [], [], []
    for source, path in automatic_sources():
        present = exists(path)
        if present is False:
            continue
        try:
            if present is None:
                raise no_access(path)
            found = read_sqlite(path, source)
        except AppError as error:
            problems.append(
                {"source": source, "path": str(path), "code": error.code, "message": error.message}
            )
            continue
        records += found
        sources.append({"source": source, "label": LABELS[source], "path": str(path)})
        sources[-1]["songs"] = len(merge(found))
    return records, sources, problems


def load(source: str | None = None, since: str | None = None, workspace=None) -> dict:
    """Shazam records, merged and oldest first, from ``source`` (None: this Mac's history;
    ``apple-music``; or a path) since ``since`` (None, ``new`` or a date).

    Raises SHAZAM_HISTORY_NOT_FOUND (with the setup steps) or SHAZAM_ACCESS_DENIED when
    nothing could be read. An empty ``records`` means nothing matched ``since``.
    """
    problems: list[dict] = []
    if source == APPLE_MUSIC:
        records = read_apple_music()
        sources = [{"source": "apple_music", "label": LABELS["apple_music"], "path": None}]
    elif source:
        path = Path(source).expanduser().absolute()
        present = exists(path)
        if present is None:
            raise no_access(path)
        if not present:
            raise AppError("FILE_UNAVAILABLE", f"Can't find {source}. Check the path.")
        try:
            kind_read, records = read_path(path)
        except PermissionError as exc:
            raise no_access(path) from exc
        sources = [{"source": kind_read, "label": LABELS[kind_read], "path": str(path)}]
    else:
        records, sources, problems = read_automatic()
        if not sources:
            denied = [p for p in problems if p["code"] == "SHAZAM_ACCESS_DENIED"]
            if denied:
                raise no_access(Path(denied[0]["path"]))
            if problems:
                raise AppError(problems[0]["code"], problems[0]["message"])
            raise not_found()
        if not records:
            raise not_found(f"{LABELS[sources[0]['source']]} has no songs yet.")
    if not records:
        raise AppError("SHAZAM_HISTORY_NOT_FOUND", f"{sources[0]['label']} has no Shazams.")
    total = len(merge(records))
    for entry in sources:
        entry.setdefault("songs", total)  # songs, like every count here, not single Shazams
    moment, inclusive, previous = since_moment(since, workspace)
    # Filter single Shazams before merging, so a song Shazamed again later counts as new.
    chosen = (
        records if moment is None else after(unseen(records, previous), moment, inclusive=inclusive)
    )
    return {
        "since": None if moment is None else moment.astimezone(UTC).isoformat(timespec="seconds"),
        "new": bool(since and since.strip().lower() == "new"),
        "last_import": previous,
        "sources": sources,
        "problems": problems,
        "total": total,
        "newest_in_history": newest(records),
        "records": merge(chosen),
    }


def newest(records: list[dict]) -> str | None:
    """The latest Shazam time among the records (merged songs count their last Shazam)."""
    times = (item.get("last_shazamed_at") or item["shazamed_at"] for item in records)
    return max(filter(None, times), default=None)


# -- status ------------------------------------------------------------------------------------


def mac_summary() -> dict:
    """The Mac's Music Recognition history at a glance, with the command that fixes it when
    it has no songs: SETUP_COMMAND shows the setup steps."""
    path = mac_history()
    summary = {
        "path": str(path),
        "found": False,
        "songs": None,
        "newest_shazamed_at": None,
        "problem": None,
        "fix": SETUP_COMMAND,
    }
    present = exists(path)
    if present is None:
        return {**summary, "problem": "SHAZAM_ACCESS_DENIED", "fix": FULL_DISK_SETTINGS}
    if not present:
        return summary
    try:
        records = merge(read_sqlite(path, "mac"))
    except AppError as error:
        fix = FULL_DISK_SETTINGS if error.code == "SHAZAM_ACCESS_DENIED" else None
        return {**summary, "found": True, "problem": error.code, "fix": fix}
    return {
        **summary,
        "found": True,
        "songs": len(records),
        "newest_shazamed_at": newest(records),
        "fix": None if records else SETUP_COMMAND,
    }


def status(workspace=None) -> dict:
    """Which Shazam sources exist on this Mac, their songs, and the last import."""
    sources = []
    raw: list[dict] = []
    for source, path in automatic_sources():
        entry = {
            "source": source,
            "label": LABELS[source],
            "path": str(path),
            "found": False,
            "songs": None,
            "newest_shazamed_at": None,
            "problem": None,
        }
        present = exists(path)
        if present is None:
            entry["problem"] = {"code": "SHAZAM_ACCESS_DENIED", "message": FULL_DISK_ACCESS}
        elif present:
            entry["found"] = True
            try:
                read = read_sqlite(path, source)
            except AppError as error:
                entry["problem"] = {"code": error.code, "message": error.message}
            else:
                raw += read
                found = merge(read)
                entry.update(songs=len(found), newest_shazamed_at=newest(found))
        sources.append(entry)
    backup = {
        "source": "iphone_backup",
        "label": "iPhone backups (Finder)",
        "path": str(backups_folder()),
        "found": False,
        "backups": [],
        "problem": None,
    }
    try:
        backup["backups"] = [str(path) for path in backups()]
        backup["found"] = bool(backup["backups"])
    except AppError as error:
        backup["problem"] = {"code": error.code, "message": error.message}
    sources.append(backup)
    history = merge(raw)
    previous = last_import(workspace)
    # Backups always need Full Disk Access; say so only when the history itself does.
    denied = any(
        (entry["problem"] or {}).get("code") == "SHAZAM_ACCESS_DENIED"
        for entry in sources
        if entry["source"] != "iphone_backup"
    )
    fresh = None
    # With the history unread or gone, what's new is unknown, not 0.
    if previous and history and not denied:
        mark = previous.get("newest_shazamed_at")
        fresh = (
            len(merge(after(unseen(raw, previous), datetime.fromisoformat(mark), inclusive=False)))
            if mark
            else len(history)
        )
    # A history macOS won't open is there: the fix is Full Disk Access, not the iCloud steps.
    setup = not history and not denied
    return {
        "ready": bool(history),
        "songs": len(history),
        "newest_shazamed_at": newest(history),
        # What to check first for "my recent Shazams": `djlib set shazam` alone is the whole
        # history, oldest first.
        "recent_spec": f"shazam:{recent_since(newest(history))[0]}" if history else None,
        "sources": sources,
        "last_import": previous,
        "new_since_last_import": fresh,
        "setup": SETUP if setup else [],
        "alternatives": ALTERNATIVES if setup else [],
        "full_disk_access": FULL_DISK_ACCESS if denied else None,
    }
