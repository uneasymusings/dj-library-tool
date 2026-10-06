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
    comments: int = Field(default=0, ge=0, le=2000)


class SourceSearch(Contract):
    artist: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)
    limit: int = Field(default=8, ge=1, le=20)


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


def base_title(title: str) -> str:
    """The title without a trailing bracketed mix name."""
    match = TRAILING_VERSION.match(title)
    return match["title"] if match else title


def base_form(artist: str, title: str) -> tuple[str, str]:
    """Same song, any version: artist plus the title without its trailing mix name."""
    return normalize(artist), normalize(base_title(title))


def version_prefixes(artist: str, title: str, version: str = "") -> list[str]:
    """Catalog key prefixes that can hold a recording with equivalent labels."""
    prefixes = [recording_key(artist, title)]
    if version.strip():
        prefixes.append(recording_key(artist, f"{title} {version}"))
    elif (base := base_title(title)) != title:
        prefixes.append(recording_key(artist, base))
    return list(dict.fromkeys(prefixes))


def related_prefixes(artist: str, title: str) -> list[str]:
    """Prefixes for other versions of the same song, e.g. "Night Bus (Radio Edit)"."""
    base = recording_key(artist, base_title(title))
    return [base, base.removesuffix("|") + " "]


# Credit separators between artist names: "A, B", "A & B", "A x B", "A feat. B", "A vs B".
# A lowercase "x" only, so a capital X inside a name is left alone.
CREDIT_SEPARATOR = re.compile(
    r"\s*[,;/]\s*|\s+&\s+|\s+x\s+|\s+(?i:featuring|feat\.?|ft\.?|vs\.?)\s+"
)
_FEATURING = r"(?i:featuring\s+|feat(?:\.\s*|\s+)|ft(?:\.\s*|\s+))"
# "Title (feat. X)" / "Title [ft. X]" anywhere in the title, or a bare "Title feat. X" that
# runs to the end or to the next bracketed part ("Title feat. X (Extended Mix)").
FEATURED = re.compile(
    rf"\s*[\(\[]\s*{_FEATURING}(?P<bracketed>[^\(\)\[\]]+?)\s*[\)\]]"
    rf"|\s+{_FEATURING}(?P<bare>[^\(\)\[\]]+?)(?=\s*(?:[\(\[]|$))"
)


def split_featured(title: str) -> tuple[str, tuple[str, ...]]:
    """The title without featuring credits, plus those credits as written.

    "Rain (feat. Ana) (Dub)" becomes ("Rain (Dub)", ("Ana",)).
    """
    featured = tuple(match["bracketed"] or match["bare"] for match in FEATURED.finditer(title))
    rest = FEATURED.sub("", title).strip()
    return (rest, featured) if rest else (title, ())


def artist_names(artist: str, *featured: str) -> tuple[str, ...]:
    """Credited names as a sorted set: "Max Dean, Luke Dean & Jamie Jones" has three names.

    Featured credits written in a title are passed as ``featured``. This is label matching
    only; identity keys keep the artist string as written.
    """
    rest, inline = split_featured(artist)
    names = {
        normalize(name)
        for part in (rest, *inline, *featured)
        for name in CREDIT_SEPARATOR.split(part)
    }
    names.discard("")
    # Symbol-only credits such as "/" split into nothing; keep the whole label instead.
    return tuple(sorted(names)) or (normalize(artist),)


def credit_form(artist: str, title: str, version: str = "") -> tuple[tuple[str, ...], str]:
    """``label_form`` with the artist as a set of names, featured credits moved out of the title.

    "Max Dean, Luke Dean, Jamie Jones" equals "Jamie Jones & Max Dean & Luke Dean", and
    "A feat. B" + "T" equals "A" + "T (feat. B)". A subset of the names is not equal.
    """
    title, featured = split_featured(title)
    return artist_names(artist, *featured), label_form("", title, version)[1]


def credit_base_form(artist: str, title: str) -> tuple[tuple[str, ...], str]:
    """``base_form`` with credited names as a set: same song, same artists, any version."""
    title, featured = split_featured(title)
    return artist_names(artist, *featured), normalize(base_title(title))


def title_lookup(artist: str, title: str) -> tuple[list[str], list[str]]:
    """Identity-key fragments shared by tagged recordings of this song in any artist order.

    Returns the title-segment fragments (the base title alone, or followed by more words
    such as a mix name or a featuring credit) and each credited name that must appear
    somewhere in the key. Callers still compare ``credit_base_form`` exactly.
    """
    rest, featured = split_featured(title)
    segment = recording_key("", base_title(rest)).split("|")[1]
    names = [name for name in artist_names(artist, *featured) if re.search(r"\w", name)]
    return [f"|{segment}|", f"|{segment} "], names


def version_markers(value: str) -> frozenset[str]:
    """Detect obvious incompatible edits; this is not acoustic identification."""
    text = normalize(value)
    return frozenset(
        marker
        for marker in ("extended", "radio", "dub", "instrumental", "live", "remix")
        if marker in text.split()
    )
