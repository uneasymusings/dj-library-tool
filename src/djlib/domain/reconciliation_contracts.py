"""Explicit revision and byte expectations for changed catalog locations."""

from typing import Literal

from pydantic import Field, model_validator

from djlib.domain.contracts import Contract


class ReconcileItem(Contract):
    path: str = Field(min_length=1, max_length=4096)
    expected_asset_revision_id: str = Field(min_length=1, max_length=200)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["tag_only", "replace_audio"]


class ReconcileRequest(Contract):
    items: list[ReconcileItem] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_paths(self):
        if len({item.path for item in self.items}) != len(self.items):
            raise ValueError("Each changed path may appear only once.")
        return self
