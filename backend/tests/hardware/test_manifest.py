from typing import Any

import pytest
from pydantic import ValidationError

from firmloop.hardware.manifest import ChannelSpec, Manifest


def channel(**kw: Any) -> dict[str, Any]:
    return {"id": "c", "type": "float", **kw}


def manifest(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "m",
        "name": "M",
        "kind": "sensor",
        "pins": {"a": 1},
        "driver": {"header": "driver.h", "class": "MDriver"},
        "channels": [channel()],
    }
    return {**base, **kw}


def test_valid_channel_ok() -> None:
    ChannelSpec.model_validate(channel(range=[0, 10], neutral=5.0))


@pytest.mark.parametrize("rng", [[5, 5], [10, 0]])
def test_range_must_be_increasing(rng: list[float]) -> None:
    with pytest.raises(ValidationError, match="range"):
        ChannelSpec.model_validate(channel(range=rng))


@pytest.mark.parametrize("type_", ["float", "int"])
def test_neutral_within_range(type_: str) -> None:
    with pytest.raises(ValidationError, match="neutral"):
        ChannelSpec.model_validate(channel(type=type_, range=[0, 10], neutral=11))


@pytest.mark.parametrize(
    ("type_", "neutral"),
    [("bool", 1.0), ("bool", "x"), ("text", 1.0), ("text", True), ("float", "x"),
     ("float", True), ("int", False)],
)  # fmt: skip
def test_neutral_type_matches(type_: str, neutral: object) -> None:
    with pytest.raises(ValidationError, match="neutral"):
        ChannelSpec.model_validate(channel(type=type_, neutral=neutral))


@pytest.mark.parametrize("type_,neutral", [("bool", True), ("text", ""), ("int", 3), ("float", 3)])
def test_neutral_type_ok(type_: str, neutral: object) -> None:
    ChannelSpec.model_validate(channel(type=type_, neutral=neutral))


@pytest.mark.parametrize(
    "field", ["range", "plausible", "neutral", "resolution", "default_hysteresis"]
)
@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_floats_must_be_finite(field: str, bad: float) -> None:
    value: object = [0.0, bad] if field in ("range", "plausible") else bad
    with pytest.raises(ValidationError):
        ChannelSpec.model_validate(channel(**{field: value}))


def test_rows_cols_only_on_display() -> None:
    with pytest.raises(ValidationError, match="rows"):
        Manifest.model_validate(manifest(rows=4))
    with pytest.raises(ValidationError, match="cols"):
        Manifest.model_validate(manifest(cols=4))
    Manifest.model_validate(
        manifest(kind="display", rows=4, cols=21, channels=[channel(type="text", neutral="")])
    )
