"""Portable DJ handoff artifacts and read-only storage preflight.

Generated XML describes a collection; it does not prove app import or player readiness.
"""

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from djlib.domain.errors import AppError


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def rekordbox_xml(snapshot: dict) -> str:
    """Follow AlphaTheta's published XML interchange structure, without fabricated analysis."""
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="DJ Library Tool", Version="0.1", Company="uneasymusings")
    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(snapshot["tracks"])))
    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="1")
    playlist = ET.SubElement(
        folder,
        "NODE",
        Type="1",
        Name=snapshot["name"],
        KeyType="0",
        Entries=str(len(snapshot["tracks"])),
    )
    for number, track in enumerate(snapshot["tracks"], 1):
        uri = Path(track["path"]).as_uri()
        if not uri.startswith("file:///"):
            raise AppError(
                "EXPORT_LOCATION_UNSUPPORTED",
                "Copy network media into local managed storage before XML export.",
            )
        # rekordbox's interchange contract requires the explicit localhost authority.
        location = uri.replace("file:///", "file://localhost/", 1)
        ET.SubElement(
            collection,
            "TRACK",
            TrackID=str(number),
            Name=track["title"],
            Artist=track["artist"],
            Mix=track["version"],
            TotalTime=str(round(track["properties"]["duration_seconds"])),
            Location=location,
        )
        ET.SubElement(playlist, "TRACK", Key=str(number))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def device_preflight(value: str, required_bytes: int = 0) -> dict:
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError
        usage = shutil.disk_usage(path)
    except (OSError, ValueError) as exc:
        raise AppError(
            "DEVICE_UNAVAILABLE", "Choose an existing mounted storage directory."
        ) from exc
    return {
        "path": str(path),
        "is_mount_point": path.is_mount(),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "required_bytes": required_bytes,
        "has_requested_space": usage.free >= required_bytes + 64 * 1024 * 1024,
        "filesystem": "not_detected",
        "player_compatibility": "not_verified",
        "device_state": "not_exported",
        "writes_performed": False,
        "next_step": "Confirm player/filesystem, then export the imported playlist in rekordbox.",
    }
