"""Pydantic models for ``hardware/<id>/manifest.yaml``."""

import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChannelType = Literal["float", "int", "bool", "text"]
Kind = Literal["sensor", "actuator", "display"]

ID_PATTERN = r"^[a-z0-9_]+$"

Scalar = float | int | bool | str


class ChannelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    type: ChannelType
    unit: str | None = None
    resolution: float | None = None
    range: list[float] | None = Field(default=None, min_length=2, max_length=2)
    plausible: list[float] | None = Field(default=None, min_length=2, max_length=2)
    neutral: Scalar | None = None
    default_hysteresis: float | None = None
    injectable: bool = False

    @model_validator(mode="after")
    def _check_values(self) -> Self:
        for name in ("resolution", "default_hysteresis"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name in ("range", "plausible"):
            pair = getattr(self, name)
            if pair is not None and not all(math.isfinite(v) for v in pair):
                raise ValueError(f"{name} values must be finite")
        if self.range is not None and not self.range[0] < self.range[1]:
            raise ValueError("range[0] must be less than range[1]")
        n = self.neutral
        if n is None:
            return self
        if self.type == "bool":
            ok = isinstance(n, bool)
        elif self.type == "text":
            ok = isinstance(n, str)
        else:
            ok = isinstance(n, int | float) and not isinstance(n, bool)
        if not ok:
            raise ValueError(f"neutral {n!r} does not match channel type '{self.type}'")
        if self.type in ("float", "int"):
            assert not isinstance(n, str)
            if not math.isfinite(n):
                raise ValueError("neutral must be finite")
            if self.range is not None and not self.range[0] <= n <= self.range[1]:
                raise ValueError(f"neutral {n!r} is outside range {self.range}")
        return self


class DriverSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    header: str
    class_: str = Field(alias="class", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    libraries: list[str] = []


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str
    kind: Kind
    pins: dict[str, int]
    driver: DriverSpec
    sample_interval_ms: int | None = Field(default=None, gt=0)
    active_low: bool = False
    rows: int | None = None
    cols: int | None = None
    channels: list[ChannelSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_display_fields(self) -> Self:
        if self.kind != "display":
            for name in ("rows", "cols"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{name} is only allowed on display components")
        return self


class Channel(ChannelSpec):
    """A channel together with the component that provides it."""

    component: str
    kind: Kind
