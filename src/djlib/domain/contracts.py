"""Version-one contracts shared by CLI, HTTP, MCP, and persisted plans.

Strict requests prevent an assistant typo from silently changing acquisition behavior.
Media paths remain subject to workspace authorization after schema validation.
"""

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

SCHEMA_VERSION = "1"


class Contract(BaseModel):
    """Base for public inputs; unknown options are always rejected."""

    model_config = ConfigDict(extra="forbid")


class ResponseEnvelope(Contract):
    """Typed MCP output; transports preserve the same envelope fields."""

    schema_version: Literal["1"]
    ok: bool
    request_id: str
    result: dict[str, Any] | None
    warnings: list[str]
    error: dict[str, Any] | None


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


class DeliveryRequest(Contract):
    name: str = Field(min_length=1, max_length=200)
    collection_ids: list[str] = Field(min_length=1, max_length=100)
    workflow: Literal["rekordbox_import", "serato_import", "rekordbox_usb", "serato_portable"]
    app_version: str = Field(min_length=1, max_length=100)
    hardware_profile: str | None = None
    audio_mode: Literal["preserve", "mp3_320", "wav16_44100"] = "preserve"
    phase: Literal["pilot", "full"] = "pilot"
    pilot_size: int = Field(default=3, ge=1, le=5)
    pilot_delivery_id: str | None = None

    @field_validator("name", "app_version", "hardware_profile")
    @classmethod
    def target_labels(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("Delivery labels must be nonblank and contain no control characters.")
        return value

    @model_validator(mode="after")
    def validate_target(self):
        if self.workflow == "rekordbox_usb" and not self.hardware_profile:
            raise ValueError("A player hardware profile is required for standalone USB export.")
        if self.workflow != "rekordbox_usb" and self.hardware_profile is not None:
            raise ValueError("A player profile applies only to standalone rekordbox USB export.")
        if len(set(self.collection_ids)) != len(self.collection_ids):
            raise ValueError("Collection IDs must be unique.")
        if any(ord(c) < 32 for c in self.name):
            raise ValueError("Names cannot contain control characters.")
        return self


class DeliveryPrepareRequest(Contract):
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class DeliveryDeviceRequest(Contract):
    revision: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=4096)


class DeliveryObservation(Contract):
    revision: int = Field(ge=1)
    stage: Literal[
        "imported", "analyzed", "native_exported", "device_library_checked", "hardware_playback"
    ]
    app_version: str = Field(min_length=1, max_length=100)
    track_count: int = Field(ge=0)
    playlist_counts: dict[str, int] = Field(max_length=100)
    checked_recording_ids: list[str] = Field(max_length=10_000)
    observer: str = Field(min_length=1, max_length=200)
    notes: str = Field(min_length=1, max_length=4000)
    # Explicit operator observations, never inferred from artifact or database filenames.
    method: Literal["native_app_ui", "physical_hardware"]
    outcome: Literal["passed", "failed"]
    hardware_profile: str | None = None
    firmware_version: str | None = Field(default=None, min_length=1, max_length=100)
    storage_recognized: bool | None = None

    @field_validator("app_version", "observer", "firmware_version")
    @classmethod
    def evidence_labels(cls, value):
        return DeliveryRequest.target_labels(value)

    @field_validator("playlist_counts")
    @classmethod
    def nonnegative_counts(cls, value):
        if any(not key.strip() or len(key) > 200 or count < 0 for key, count in value.items()):
            raise ValueError("Playlist counts need bounded IDs and nonnegative counts.")
        return value

    @field_validator("checked_recording_ids")
    @classmethod
    def recording_identifiers(cls, value):
        if len(set(value)) != len(value) or any(
            not item.strip() or len(item) > 200 for item in value
        ):
            raise ValueError("Checked recording IDs must be unique, nonblank and bounded.")
        return value


class DeliveryVerifyRequest(Contract):
    revision: int = Field(ge=1)


class DeliveryNativeXMLRequest(Contract):
    revision: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=4096)


def normalize(value: str) -> str:
    """Normalize labels for conservative text matching, retaining mix qualifiers."""
    value = unicodedata.normalize("NFKC", value).casefold()
    words = " ".join(re.sub(r"[^\w]+", " ", value).split())
    # Symbol-only names are valid musical labels. An empty normalized name would
    # silently merge every such artist/title, including unrelated recordings.
    return words or " ".join(value.split())


def recording_key(artist: str, title: str, version: str = "") -> str:
    # Escape delimiters in preserved symbol-only labels without changing existing
    # ordinary text keys. Catalog upgrades deliberately do not rewrite old IDs.
    return "|".join(
        normalize(part).replace("%", "%25").replace("|", "%7C") for part in (artist, title, version)
    )


TRAILING_VERSION = re.compile(r"^(?P<title>.*\S)\s*[\(\[](?P<version>[^\)\]]+)[\)\]]\s*$")


def label_form(artist: str, title: str, version: str = "") -> tuple[str, str]:
    """Labels compared as written: "Rain (Extended Mix)" equals "Rain" + "Extended Mix".

    Taggers store mix names either inside the title or in a separate version field. This is
    label equivalence for matching only; identity keys and stored IDs are unchanged.
    """
    return normalize(artist), normalize(f"{title} {version}" if version.strip() else title)


def version_prefixes(artist: str, title: str, version: str = "") -> list[str]:
    """Catalog key prefixes that can hold a recording with equivalent labels."""
    prefixes = [recording_key(artist, title)]
    if version.strip():
        prefixes.append(recording_key(artist, f"{title} {version}"))
    elif match := TRAILING_VERSION.match(title):
        prefixes.append(recording_key(artist, match["title"]))
    return list(dict.fromkeys(prefixes))


def version_markers(value: str) -> frozenset[str]:
    """Detect obvious incompatible edits; this is not acoustic identification."""
    text = normalize(value)
    return frozenset(
        marker
        for marker in ("extended", "radio", "dub", "instrumental", "live", "remix")
        if marker in text.split()
    )
