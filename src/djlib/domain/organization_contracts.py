"""Explicit catalog organization; no acoustic analysis or label-based identity guessing."""

import math
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from djlib.domain.contracts import Contract


def clean_key(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"(?:[1-9]|1[0-2])[ABdm]|[A-Ga-g][#b]?(?:m|maj|min|major|minor)?", value):
        raise ValueError("Use an explicit musical, Camelot, or Open Key label; no key guessing.")
    return value


class RecordingReference(Contract):
    recording_id: str = Field(min_length=1, max_length=200)
    asset_revision_id: str = Field(min_length=1, max_length=200)


class BPMAnnotation(Contract):
    value: float = Field(ge=20, le=400)
    source: Literal["operator", "native_tag"]
    verified: bool = False

    @field_validator("value")
    @classmethod
    def finite(cls, value):
        if not math.isfinite(value):
            raise ValueError("BPM must be finite.")
        return value


class KeyAnnotation(Contract):
    value: str = Field(min_length=1, max_length=32)
    source: Literal["operator", "native_tag"]
    verified: bool = False

    @field_validator("value")
    @classmethod
    def musical_key(cls, value):
        return clean_key(value)


class AnnotationRequest(RecordingReference):
    """Patch supplied fields only; null clears a field. Revision zero creates a row."""

    revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    genres: list[str] | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=50)
    set_role: str | None = Field(default=None, max_length=80)
    energy: int | None = Field(default=None, ge=1, le=10)
    bpm: BPMAnnotation | None = None
    key: KeyAnnotation | None = None

    @field_validator("genres", "tags")
    @classmethod
    def labels(cls, values):
        if values is None:
            return None
        result = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ValueError("Genre/tag labels must be nonblank, bounded, and single-line.")
            if value.casefold() not in {s.casefold() for s in result}:
                result.append(value)
        return result

    @field_validator("set_role")
    @classmethod
    def role(cls, value):
        if value is not None:
            value = value.strip()
            if not value or any(ord(c) < 32 for c in value):
                raise ValueError("Set role must be nonblank and single-line.")
        return value

    @model_validator(mode="after")
    def has_patch(self):
        if not self.model_fields_set.intersection(
            {"notes", "genres", "tags", "set_role", "energy", "bpm", "key"}
        ):
            raise ValueError("Supply at least one annotation field.")
        return self


class OrganizationFilters(Contract):
    bpm_min: float | None = Field(default=None, ge=20, le=400)
    bpm_max: float | None = Field(default=None, ge=20, le=400)
    keys: list[str] = Field(default_factory=list, max_length=50)
    genres: list[str] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=50)
    set_roles: list[str] = Field(default_factory=list, max_length=50)
    require_verified: bool = False

    @field_validator("keys")
    @classmethod
    def musical_keys(cls, values):
        return [clean_key(value) for value in values]

    @field_validator("genres", "tags", "set_roles")
    @classmethod
    def labels(cls, values):
        return AnnotationRequest.labels(values)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.bpm_min is not None and self.bpm_max is not None and self.bpm_min > self.bpm_max:
            raise ValueError("BPM minimum must not exceed maximum.")
        return self


class OrganizationRequest(Contract):
    name: str = Field(min_length=1, max_length=300)
    tracks: list[RecordingReference] = Field(min_length=1, max_length=1000)
    filters: OrganizationFilters = Field(default_factory=OrganizationFilters)
    unknown: Literal["exclude", "include", "error"] = "exclude"
    order_by: Literal["input", "artist", "title", "bpm", "key", "energy"] = "input"
    descending: bool = False
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def name_label(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Collection names must be nonblank and single-line.")
        return value
