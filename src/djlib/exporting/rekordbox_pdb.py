"""Read-only reader for the ``export.pdb`` device library that rekordbox writes to a USB.

This is the database Pioneer players load: playlists, their entries and the track rows
with each file's path on the stick. Only the tables djlib needs are decoded (tracks, keys,
artists, playlist tree, playlist entries). The layout follows the community documentation
of the DeviceSQL format (Deep Symmetry's crate-digger); nothing is ever written.
"""

import struct
from pathlib import Path

from djlib.domain.errors import AppError

TRACKS, ARTISTS, KEYS, PLAYLIST_TREE, PLAYLIST_ENTRIES = 0, 2, 5, 7, 8
PAGE_HEADER = 0x28
ROW_GROUP = 0x24
MAX_BYTES = 256 * 1024 * 1024
TRACK_STRINGS = {17: "title", 19: "file_name", 20: "file_path", 12: "mix_name"}


def _string(data: bytes, offset: int) -> str:
    """A DeviceSQL string: short ASCII, long ASCII or long UTF-16LE."""
    if offset >= len(data):
        return ""
    kind = data[offset]
    if kind & 1:
        length = (kind >> 1) - 1
        return data[offset + 1 : offset + 1 + length].decode("ascii", "replace")
    if kind in (0x40, 0x90):
        (length,) = struct.unpack_from("<H", data, offset + 1)
        body = data[offset + 4 : offset + length]
        if kind == 0x90:
            return body.decode("utf-16-le", "replace")
        return body.decode("ascii", "replace")
    return ""


class DeviceLibrary:
    def __init__(self, data: bytes):
        if len(data) < 28:
            raise AppError("DEVICE_LIBRARY_INVALID", "export.pdb is too short.")
        _, self.page_size, tables = struct.unpack_from("<III", data, 0)
        if self.page_size not in (4096, 8192, 16384) or not 0 < tables < 64:
            raise AppError("DEVICE_LIBRARY_INVALID", "export.pdb has an unknown layout.")
        self.data = data
        self.tables = {}
        for index in range(tables):
            kind, _, first, last = struct.unpack_from("<IIII", data, 28 + 16 * index)
            self.tables[kind] = (first, last)

    def rows(self, kind: int):
        """Yield (page_offset, row_offset) for every present row of one table."""
        if kind not in self.tables:
            return
        page, last = self.tables[kind]
        seen = set()
        while page not in seen and (page + 1) * self.page_size <= len(self.data):
            seen.add(page)
            base = page * self.page_size
            page_type, next_page = struct.unpack_from("<II", self.data, base + 8)
            small, flags = self.data[base + 0x18], self.data[base + 0x1B]
            (large,) = struct.unpack_from("<H", self.data, base + 0x22)
            if page_type == kind and not flags & 0x40:
                count = large if small < large != 0x1FFF else small
                for group in range((count + 15) // 16):
                    end = base + self.page_size - group * ROW_GROUP
                    (present,) = struct.unpack_from("<H", self.data, end - 4)
                    for slot in range(min(16, count - 16 * group)):
                        if present >> slot & 1:
                            (offset,) = struct.unpack_from("<H", self.data, end - 6 - 2 * slot)
                            yield base, base + PAGE_HEADER + offset
            if page == last:
                break
            page = next_page

    def keys(self) -> dict[int, str]:
        return {
            struct.unpack_from("<I", self.data, row)[0]: _string(self.data, row + 8)
            for _, row in self.rows(KEYS)
        }

    def artists(self) -> dict[int, str]:
        names = {}
        for _, row in self.rows(ARTISTS):
            subtype, _, artist_id = struct.unpack_from("<HHI", self.data, row)
            if subtype == 0x64:
                (offset,) = struct.unpack_from("<H", self.data, row + 0x0A)
            else:
                offset = self.data[row + 9]
            names[artist_id] = _string(self.data, row + offset)
        return names

    def tracks(self) -> dict[int, dict]:
        keys, artists = self.keys(), self.artists()
        tracks = {}
        for _, row in self.rows(TRACKS):
            size, key_id, tempo, artist_id, track_id = (
                struct.unpack_from("<I", self.data, row + offset)[0]
                for offset in (0x10, 0x20, 0x38, 0x44, 0x48)
            )
            offsets = struct.unpack_from("<21H", self.data, row + 0x5E)
            track = {
                name: _string(self.data, row + offsets[i]) for i, name in TRACK_STRINGS.items()
            }
            track.update(
                id=track_id,
                file_size=size,
                bpm=tempo / 100 if tempo else None,
                key=keys.get(key_id) or None,
                artist=artists.get(artist_id) or None,
            )
            tracks[track_id] = track
        return tracks

    def playlists(self) -> list[dict]:
        """Every playlist (not folder) with its track IDs in entry order."""
        entries: dict[int, list[tuple[int, int]]] = {}
        for _, row in self.rows(PLAYLIST_ENTRIES):
            index, track_id, playlist_id = struct.unpack_from("<III", self.data, row)
            entries.setdefault(playlist_id, []).append((index, track_id))
        playlists = []
        for _, row in self.rows(PLAYLIST_TREE):
            parent, _, _, playlist_id, folder = struct.unpack_from("<IIIII", self.data, row)
            if folder:
                continue
            playlists.append(
                {
                    "id": playlist_id,
                    "parent_id": parent,
                    "name": _string(self.data, row + 0x14),
                    "track_ids": [track for _, track in sorted(entries.get(playlist_id, []))],
                }
            )
        return playlists


def read(volume: Path) -> DeviceLibrary:
    path = volume / "PIONEER" / "rekordbox" / "export.pdb"
    try:
        if path.stat().st_size > MAX_BYTES:
            raise AppError("DEVICE_LIBRARY_INVALID", "export.pdb is unexpectedly large.")
        return DeviceLibrary(path.read_bytes())
    except OSError as exc:
        raise AppError(
            "DEVICE_LIBRARY_MISSING", "The USB has no rekordbox device library yet."
        ) from exc


def playlist(volume: Path, name: str) -> dict | None:
    """The named playlist as the player sees it: ordered tracks with their paths on the stick.

    When several playlists share the name (rekordbox allows that), the largest one wins.
    """
    library = read(volume)
    matches = [item for item in library.playlists() if item["name"] == name]
    if not matches:
        return None
    chosen = max(matches, key=lambda item: len(item["track_ids"]))
    tracks = library.tracks()
    return {
        "name": chosen["name"],
        "same_name_playlists": len(matches),
        "tracks": [tracks.get(track_id, {"id": track_id}) for track_id in chosen["track_ids"]],
    }
