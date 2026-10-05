"""Strict, bounded song-list inputs; unknown recordings remain explicit evidence."""

import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from djlib.domain.contracts import Contract


class RequestContract(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)


def clean_text(value: str) -> str:
    value = value.strip()
    if not value or any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("Use nonblank text without control characters.")
    return value


def evidence_url(value: str) -> str:
    value = clean_text(value)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Source evidence must be an HTTPS URL without credentials.")
    return value


class RequestItem(RequestContract):
    kind: Literal["named", "unknown"] = "named"
    artist: str | None = Field(default=None, min_length=1, max_length=500)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    version: str = Field(default="", max_length=300)
    label: str | None = Field(default=None, min_length=1, max_length=1000)
    timestamp: str | None = Field(default=None, min_length=1, max_length=100)
    source_url: str | None = Field(default=None, min_length=1, max_length=4096)
    notes: str = Field(default="", max_length=4000)

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
            or not (self.timestamp or self.source_url)
            or self.artist is not None
            or self.title is not None
            or self.version
        ):
            raise ValueError(
                "Unknown requests require a label and timestamp or source URL; "
                "do not supply guessed artist/title/version labels."
            )
        return self


class RequestCreate(RequestContract):
    name: str = Field(min_length=1, max_length=300)
    items: list[RequestItem] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("name", "idempotency_key")
    @classmethod
    def text(cls, value):
        return clean_text(value)


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
