"""Builds small ``export.pdb`` files in the DeviceSQL layout rekordbox writes to USB sticks."""

import struct

PAGE = 4096


def text(value: str) -> bytes:
    if value.isascii() and len(value) < 126:
        return bytes([((len(value) + 1) << 1) | 1]) + value.encode("ascii")
    body = value.encode("utf-16-le")
    return bytes([0x90]) + struct.pack("<H", len(body) + 4) + b"\0" + body


def key_row(key_id: int, name: str) -> bytes:
    return struct.pack("<II", key_id, key_id) + text(name)


def artist_row(artist_id: int, name: str) -> bytes:
    return struct.pack("<HHIBB", 0x60, 0, artist_id, 3, 10) + text(name)


def track_row(track_id, title, file_path, size, bpm, key_id, artist_id) -> bytes:
    header = bytearray(0x88)
    struct.pack_into("<HH", header, 0, 0x24, 0)
    struct.pack_into("<I", header, 0x10, size)
    struct.pack_into("<I", header, 0x20, key_id)
    struct.pack_into("<I", header, 0x38, round(bpm * 100))
    struct.pack_into("<I", header, 0x44, artist_id)
    struct.pack_into("<I", header, 0x48, track_id)
    strings = {17: title, 19: file_path.rsplit("/", 1)[-1], 20: file_path}
    heap = bytearray(text(""))  # every other string is empty
    offsets = [0x88] * 21
    for index, value in strings.items():
        offsets[index] = 0x88 + len(heap)
        heap += text(value)
    struct.pack_into("<21H", header, 0x5E, *offsets)
    return bytes(header + heap)


def tree_row(playlist_id: int, name: str, folder: bool = False, parent: int = 0) -> bytes:
    return struct.pack("<IIIII", parent, 0, playlist_id, playlist_id, int(folder)) + text(name)


def entry_row(index: int, track_id: int, playlist_id: int) -> bytes:
    return struct.pack("<III", index, track_id, playlist_id)


def page(index: int, kind: int, rows: list[bytes], deleted: frozenset = frozenset()) -> bytes:
    data = bytearray(PAGE)
    heap, offsets = bytearray(), []
    for row in rows:
        offsets.append(len(heap))
        heap += row + b"\0" * (-len(row) % 4)
    struct.pack_into("<IIII", data, 0, 0, index, kind, index)
    data[0x18] = len(rows)
    data[0x1B] = 0x34
    data[0x28 : 0x28 + len(heap)] = heap
    for group in range((len(rows) + 15) // 16):
        end = PAGE - group * 0x24
        slots = offsets[16 * group : 16 * group + 16]
        present = sum(1 << n for n in range(len(slots)) if 16 * group + n not in deleted)
        struct.pack_into("<H", data, end - 4, present)
        for slot, offset in enumerate(slots):
            struct.pack_into("<H", data, end - 6 - 2 * slot, offset)
    return bytes(data)


def library(tables: dict[int, list[bytes]], deleted: dict[int, frozenset] | None = None) -> bytes:
    deleted = deleted or {}
    header = bytearray(PAGE)
    struct.pack_into("<IIIIIII", header, 0, 0, PAGE, len(tables), len(tables) + 1, 0, 1, 0)
    pages = []
    for number, (kind, rows) in enumerate(sorted(tables.items()), start=1):
        struct.pack_into("<IIII", header, 28 + 16 * (number - 1), kind, 0, number, number)
        pages.append(page(number, kind, rows, deleted.get(kind, frozenset())))
    return bytes(header) + b"".join(pages)


def stick(volume, playlist: str, files: list[tuple[str, bytes]], extra_playlists=()) -> None:
    """Write files under Contents/ and an export.pdb listing them as one playlist."""
    tracks, entries = [], []
    for number, (relative, data) in enumerate(files, start=1):
        path = volume / "Contents" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        tracks.append(
            track_row(number, f"Track {number}", f"/Contents/{relative}", len(data), 124.0, 1, 1)
        )
        entries.append(entry_row(number, number, 10))
    tree = [tree_row(1, "Folder", folder=True), tree_row(10, playlist, parent=1)]
    tree += [tree_row(20 + n, name) for n, name in enumerate(extra_playlists)]
    folder = volume / "PIONEER" / "rekordbox"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "export.pdb").write_bytes(
        library(
            {
                0: tracks,
                2: [artist_row(1, "Velvet Static")],
                5: [key_row(1, "Am")],
                7: tree,
                8: entries,
            }
        )
    )
