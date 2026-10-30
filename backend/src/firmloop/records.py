"""Plain record models stored in JSON columns and sent in events. This module imports nothing
from the rest of firmloop except the spec models, so the database layer, the agent, the
safety checker and the evaluator can all share these types without importing each other."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from firmloop.spec import models as m


class Answer(BaseModel):
    """A clarifying question the model asked and the user's reply."""

    model_config = ConfigDict(frozen=True)

    question: str
    answer: str


class Violation(BaseModel):
    line: int
    rule: str
    message: str


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int
    expectation: m.Expectation
    message: str
    observed_summary: str


class TestResult(BaseModel):
    __test__ = False  # not a pytest class

    model_config = ConfigDict(extra="forbid")

    test_id: str
    passed: bool
    failures: list[Failure] = Field(default_factory=list[Failure])
    error: str | None = None


class RunningVersion(BaseModel):
    """The version the board should run, as the UI shows it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    number: int | None
    session_id: str
    session_title: str | None


class DeviceStatus(BaseModel):
    """What the UI knows about the board: the `device.status` event and `GET /api/device`."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["real", "virtual"]
    connected: bool
    port: str | None = None
    fw_id: str | None = None
    proto: int | None = None
    lib: str | None = None
    busy: bool = False
    running_version: RunningVersion | None = None
    last_seen: str | None = None
