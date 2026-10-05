"""Explicit additive workspace permissions; no implicit music discovery."""

from typing import Annotated

from pydantic import Field, StringConstraints

from djlib.domain.contracts import Contract


class RootsRequest(Contract):
    paths: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
    ] = Field(min_length=1, max_length=100)
