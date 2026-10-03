"""Version-one contracts shared by CLI, HTTP, MCP, and persisted plans.

Strict requests prevent an assistant typo from silently changing acquisition behavior.
Media paths remain subject to workspace authorization after schema validation.
"""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

SCHEMA_VERSION = "1"


class Contract(BaseModel):
    """Base for public inputs; unknown options are always rejected."""

    model_config = ConfigDict(extra="forbid")


class Profile(Contract):
    name: str = "club"
    copy_into_library: bool = False
    exact_version: bool = True
    max_items: int = Field(default=10_000, ge=1, le=10_000)
    disk_reserve_bytes: int = Field(default=64 * 1024 * 1024, ge=0)


class TrackInput(Contract):
    path: str = Field(min_length=1, max_length=4096)
    artist: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)

    @field_validator("artist", "title", "version")
    @classmethod
    def clean_label(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if not value and info.field_name != "version":
            raise ValueError("Artist and title cannot be blank.")
        if any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("Labels cannot contain control characters.")
        return value


class CollectionRequest(Contract):
    name: str = Field(min_length=1, max_length=300)
    tracks: list[TrackInput] = Field(min_length=1, max_length=10_000)
    profile: str = "club"


class StartRequest(Contract):
    plan_id: str
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ScanRequest(Contract):
    path: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class ControlRequest(Contract):
    action: Literal["pause", "resume", "cancel", "retry"]


class ResolveRequest(Contract):
    revision: int = Field(ge=1)
    choice: Literal["accept_requested", "use_file_metadata", "skip"]


class ExportRequest(Contract):
    collection_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class SourceTrack(Contract):
    url: str = Field(min_length=1, max_length=4096)
    artist: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)

    @field_validator("artist", "title", "version")
    @classmethod
    def clean_label(cls, value: str, info: ValidationInfo) -> str:
        return TrackInput.clean_label(value, info)


class DownloadRequest(Contract):
    name: str = Field(min_length=1, max_length=300)
    tracks: list[SourceTrack] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SourceRequest(Contract):
    url: str = Field(min_length=1, max_length=4096)


class DeviceRequest(Contract):
    path: str = Field(min_length=1, max_length=4096)
    required_bytes: int = Field(default=0, ge=0)


def normalize(value: str) -> str:
    """Normalize labels for conservative text matching, retaining mix qualifiers."""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def recording_key(artist: str, title: str, version: str = "") -> str:
    return "|".join(normalize(part) for part in (artist, title, version))


def version_markers(value: str) -> frozenset[str]:
    """Detect obvious incompatible edits; this is not acoustic identification."""
    text = normalize(value)
    return frozenset(
        marker
        for marker in ("extended", "radio", "dub", "instrumental", "live", "remix")
        if marker in text.split()
    )
