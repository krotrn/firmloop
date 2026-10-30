"""Server-to-client event models, one per event type, joined in a discriminated union."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from firmloop.records import DeviceStatus, TestResult, Violation
from firmloop.spec.diff import SpecDiff
from firmloop.spec.models import Spec

PROTO = 1

JsonObject = dict[str, object]


def _now() -> datetime:
    return datetime.now(UTC)


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: datetime = Field(default_factory=_now)


class HelloEvent(_Event):
    type: Literal["hello"] = "hello"
    server: Literal["firmloop"] = "firmloop"
    proto: int = PROTO


class PongEvent(_Event):
    type: Literal["pong"] = "pong"


class Question(BaseModel):
    question: str
    reason: str


class VersionRef(BaseModel):
    id: str
    number: int


class Failure(BaseModel):
    code: str
    message: str


class RunCreatedEvent(_Event):
    type: Literal["run.created"] = "run.created"
    run: JsonObject


class RunStatusEvent(_Event):
    type: Literal["run.status"] = "run.status"
    run_id: str
    session_id: str
    status: str
    previous_status: str | None = None


class RunLogEvent(_Event):
    type: Literal["run.log"] = "run.log"
    run_id: str
    level: Literal["info", "warn", "error"]
    message: str


class RunQuestionEvent(_Event):
    type: Literal["run.question"] = "run.question"
    run_id: str
    question: Question


class RunSpecProposedEvent(_Event):
    type: Literal["run.spec_proposed"] = "run.spec_proposed"
    run_id: str
    spec: Spec
    spec_diff: SpecDiff | None = None
    boundaries: list[str] = Field(default_factory=list)
    auto_approved: bool = False


class ByOrigin(BaseModel):
    scenario: int = 0
    boundary: int = 0
    truth_table: int = 0


class RunTestsGeneratedEvent(_Event):
    type: Literal["run.tests_generated"] = "run.tests_generated"
    run_id: str
    total: int
    by_origin: ByOrigin
    from_base: int = 0


class AttemptStartedEvent(_Event):
    type: Literal["attempt.started"] = "attempt.started"
    run_id: str
    attempt_id: str
    number: int


class AttemptCodeEvent(_Event):
    type: Literal["attempt.code"] = "attempt.code"
    run_id: str
    attempt_id: str
    code: str


class AttemptSafetyEvent(_Event):
    type: Literal["attempt.safety"] = "attempt.safety"
    run_id: str
    attempt_id: str
    ok: bool
    violations: list[Violation] = Field(default_factory=list[Violation])


class AttemptCompileEvent(_Event):
    type: Literal["attempt.compile"] = "attempt.compile"
    run_id: str
    attempt_id: str
    ok: bool
    log: str = ""


class AttemptTestsEvent(_Event):
    type: Literal["attempt.tests"] = "attempt.tests"
    run_id: str
    attempt_id: str
    passed: int
    total: int
    results: list[TestResult] = Field(default_factory=list[TestResult])


class DeployBuildEvent(_Event):
    type: Literal["deploy.build"] = "deploy.build"
    run_id: str
    ok: bool
    size: int | None = None
    log: str | None = None


class DeployFlashEvent(_Event):
    type: Literal["deploy.flash"] = "deploy.flash"
    run_id: str
    ok: bool
    message: str | None = None


class VerifyCheckEvent(_Event):
    type: Literal["verify.check"] = "verify.check"
    run_id: str
    kind: Literal["hello", "self_check", "onboard_test"]
    status: Literal["passed", "failed", "skipped_virtual"]
    details: str | JsonObject | None = None


class RunFinishedEvent(_Event):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    session_id: str
    status: str
    version: VersionRef | None = None
    failure: Failure | None = None
    tests_passed: int | None = None
    tests_total: int | None = None


class DeviceStatusEvent(_Event):
    type: Literal["device.status"] = "device.status"
    status: DeviceStatus


class DeviceReconcileEvent(_Event):
    type: Literal["device.reconcile"] = "device.reconcile"
    action: Literal["reflashed", "reloaded", "none", "skipped"]
    version: JsonObject | None = None
    reason: str = ""


class DeviceTelemetryEvent(_Event):
    type: Literal["device.telemetry"] = "device.telemetry"
    mode: Literal["run", "test", "override"]
    t: int
    s: dict[str, float | int] = Field(default_factory=dict)
    a: dict[str, float | int] = Field(default_factory=dict)
    d: list[str] = Field(default_factory=list)


class DeviceErrorEvent(_Event):
    type: Literal["device.error"] = "device.error"
    code: str
    msg: str


class LastResult(BaseModel):
    understanding_pass: bool
    implementation_pass: bool


class BenchmarkProgressEvent(_Event):
    type: Literal["benchmark.progress"] = "benchmark.progress"
    benchmark_run_id: str
    done: int
    total: int
    last_task_id: str | None = None
    last_result: LastResult | None = None


class BenchmarkFinishedEvent(_Event):
    type: Literal["benchmark.finished"] = "benchmark.finished"
    benchmark_run_id: str
    status: str
    summary: JsonObject = Field(default_factory=dict)


ServerEvent = Annotated[
    HelloEvent
    | PongEvent
    | RunCreatedEvent
    | RunStatusEvent
    | RunLogEvent
    | RunQuestionEvent
    | RunSpecProposedEvent
    | RunTestsGeneratedEvent
    | AttemptStartedEvent
    | AttemptCodeEvent
    | AttemptSafetyEvent
    | AttemptCompileEvent
    | AttemptTestsEvent
    | DeployBuildEvent
    | DeployFlashEvent
    | VerifyCheckEvent
    | RunFinishedEvent
    | DeviceStatusEvent
    | DeviceReconcileEvent
    | DeviceTelemetryEvent
    | DeviceErrorEvent
    | BenchmarkProgressEvent
    | BenchmarkFinishedEvent,
    Field(discriminator="type"),
]

server_event_adapter: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)


class SubscribeMessage(BaseModel):
    type: Literal["subscribe"] = "subscribe"
    session_id: str = Field(min_length=1, max_length=64)


class UnsubscribeMessage(BaseModel):
    type: Literal["unsubscribe"] = "unsubscribe"


class PingMessage(BaseModel):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    SubscribeMessage | UnsubscribeMessage | PingMessage, Field(discriminator="type")
]

client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
