"""Strict, bounded song-list inputs; unknown recordings remain explicit evidence."""

import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_serializer, model_validator

from djlib.domain.contracts import HIDDEN, Contract


class RequestContract(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)


def clean_text(value: str) -> str:
    # Marks that reorder or hide text would disguise a name in crates and playlists.
    value = "".join(c for c in value if c not in HIDDEN).strip()
    if not value or any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("Use nonblank text without control characters.")
    return value


def evidence_url(value: str) -> str:
    value = clean_text(value)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Source evidence must be an HTTPS URL without credentials.")
    return value


class AlsoCredit(RequestContract):
    """The same song and mix under another spelling of its credits, e.g. the listing's
    "Robert Glasper" for Shazam's "Robert Glasper Experiment". Matched by the same exact rule."""

    artist: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)
    # Where this spelling comes from: mixesdb, description, 1001tracklists, file, …
    source: str = Field(default="", max_length=40)

    @field_validator("artist", "title")
    @classmethod
    def labels(cls, value):
        return clean_text(value)

    @field_validator("version", "source")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value) if value else value


class RequestItem(RequestContract):
    kind: Literal["named", "unknown"] = "named"
    artist: str | None = Field(default=None, min_length=1, max_length=500)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)
    label: str | None = Field(default=None, min_length=1, max_length=1000)
    timestamp: str | None = Field(default=None, min_length=1, max_length=100)
    source_url: str | None = Field(default=None, min_length=1, max_length=4096)
    notes: str = Field(default="", max_length=4000)
    # How sure an identification is that the set plays this song (djlib set LINK).
    confidence: Literal["confirmed", "likely", "weak", "conflict", "unidentified"] | None = None
    # Other spellings of a named song's credits that also count as owning it (MixesDB's and a
    # pasted tracklist's, say), tried in order.
    also: list[AlsoCredit] = Field(default_factory=list, max_length=3)

    @field_validator("artist", "title", "label", "timestamp")
    @classmethod
    def labels(cls, value):
        return clean_text(value) if value is not None else value

    @field_validator("version", "notes")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value) if value else value

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        return evidence_url(value) if value is not None else value

    @model_validator(mode="after")
    def identity(self):
        if self.kind == "named":
            if not self.artist or not self.title:
                raise ValueError("Named requests require both artist and title.")
        elif (
            not self.label
            or self.artist is not None
            or self.title is not None
            or self.version
            or self.also
        ):
            # An ID keeps its place in the set even without a time ("5. ID - ID").
            raise ValueError(
                "Unknown requests require a label; do not supply guessed "
                "artist/title/version labels or other spellings."
            )
        return self

    @model_serializer(mode="wrap")
    def without_empty_also(self, handler):
        # Saved lists and their idempotency keys stay as they were before `also` existed.
        data = handler(self)
        if not data.get("also"):
            data.pop("also", None)
        return data


class SetSource(RequestContract):
    """Where a set came from, shown beside it: its page (YouTube or SoundCloud, or "Shazam")."""

    url: str | None = Field(default=None, max_length=4096)
    provider: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=1000)
    uploader: str | None = Field(default=None, max_length=500)

    @field_validator("url")
    @classmethod
    def link(cls, value):
        return evidence_url(value) if value is not None else value

    @field_validator("provider", "title", "uploader")
    @classmethod
    def text(cls, value):
        return clean_text(value) if value else None


class MixesdbCredit(RequestContract):
    """The MixesDB page whose tracklist a set uses, credited and linked beside it."""

    page_title: str = Field(min_length=1, max_length=1000)
    page_url: str = Field(min_length=1, max_length=4096)
    tracklist: Literal["complete", "incomplete", "unknown"] | None = None

    @field_validator("page_title")
    @classmethod
    def text(cls, value):
        return clean_text(value)

    @field_validator("page_url")
    @classmethod
    def link(cls, value):
        return evidence_url(value)


class RequestCreate(RequestContract):
    name: str = Field(min_length=1, max_length=300)
    items: list[RequestItem] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    # Where the set came from, for people (badge, links into the set, MixesDB credit). Never
    # part of what makes two lists the same, nor of the key's request.
    source: SetSource | None = None
    mixesdb: MixesdbCredit | None = None

    @field_validator("name", "idempotency_key")
    @classmethod
    def text(cls, value):
        return clean_text(value)

    @model_serializer(mode="wrap")
    def without_empty_source(self, handler):
        # Lists without a source serialize as before these fields existed.
        data = handler(self)
        for key in ("source", "mixesdb"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class RequestRefresh(RequestContract):
    revision: int = Field(ge=1)
    item_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("item_ids")
    @classmethod
    def identifiers(cls, value):
        if value is None:
            return value
        cleaned = [clean_text(item) for item in value]
        if any(len(item) > 200 for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("Item IDs must be unique and at most 200 characters long.")
        return cleaned


class RequestResolution(RequestContract):
    revision: int = Field(ge=1)
    action: Literal["select_source", "satisfy", "clear"]
    source_url: str | None = Field(default=None, min_length=1, max_length=4096)
    recording_id: str | None = Field(default=None, min_length=1, max_length=200)
    asset_revision_id: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str = Field(default="", max_length=4000)

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        return evidence_url(value) if value is not None else value

    @field_validator("recording_id", "asset_revision_id")
    @classmethod
    def identifiers(cls, value):
        return clean_text(value) if value is not None else value

    @field_validator("notes")
    @classmethod
    def text(cls, value):
        return clean_text(value) if value else value

    @model_validator(mode="after")
    def resolution(self):
        if self.action == "select_source":
            if not self.source_url or self.recording_id or self.asset_revision_id:
                raise ValueError("Selecting a source requires only source_url and optional notes.")
        elif self.action == "satisfy":
            if not self.recording_id or self.source_url or not self.notes:
                raise ValueError(
                    "Satisfaction requires a recording ID and explicit evidence notes."
                )
        elif self.source_url or self.recording_id or self.asset_revision_id:
            raise ValueError("Clearing a resolution cannot also select a source or recording.")
        return self


class RequestCollect(RequestContract):
    """Build an ordered collection from a request list's owned tracks."""

    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator("name")
    @classmethod
    def collection_name(cls, value):
        return clean_text(value) if value is not None else value
