"""Judge one test case's snapshots against its expectations.

The same code evaluates native runs, the virtual device and the real board: every source
produces snapshots of the same shape.
"""

import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from firmloop.clients.runner import CaseResult
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m

# A number never starts inside a word or another number; "-" is a minus sign only when it does
# not directly follow a letter or digit ("Room-2" reads as 2, "T:-30" as -30).
NUMBER_PATTERN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
FLOAT_SLACK = 1e-9  # keeps 30.1 vs 30.0 within a resolution of 0.1
SUMMARY_MARGIN_MS = 100  # timeline continues this long past the window
SUMMARY_MAX_CHARS = 160


class Snapshot(BaseModel):
    """One tick: sensor values seen, actuator values, display rows and errors."""

    model_config = ConfigDict(extra="ignore")

    t: int
    s: dict[str, float | int] = Field(default_factory=dict)
    a: dict[str, float | int] = Field(default_factory=dict)
    d: list[str] = Field(default_factory=list)
    err: list[str] = Field(default_factory=list)


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


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _error_result(test_id: str, error: str) -> TestResult:
    return TestResult(test_id=test_id, passed=False, error=error)


def _status_error(run: CaseResult) -> str | None:
    if run.status == "ok":
        return None
    if run.status == "timeout":
        return "TIMEOUT"
    if run.status == "killed":
        return "KILLED"
    if run.signal:
        return f"CRASHED: {run.signal}"
    if run.exit_code is not None:
        return f"CRASHED: exit code {run.exit_code}"
    return "CRASHED: unknown"


class _Judge:
    def __init__(self, snapshots: Sequence[Snapshot], registry: Registry) -> None:
        self.snapshots = snapshots
        self.registry = registry

    def tolerance(self, exp: m.Expectation) -> float:
        source = self.registry.channels.get(exp.source_channel or "")
        resolution = source.resolution if source is not None and source.resolution else 0.0
        return max(exp.tolerance, resolution)

    def predicate(self, exp: m.Expectation) -> Callable[[Snapshot], bool]:
        if exp.op == "eq":
            return lambda snap: exp.channel in snap.a and snap.a[exp.channel] == exp.value
        if exp.op == "display_contains":
            needle = str(exp.value).lower()
            return lambda snap: any(needle in row.lower() for row in snap.d)
        if exp.op == "display_row_eq":
            row = exp.row if exp.row is not None else -1
            want = "" if exp.value is None else str(exp.value)
            return lambda snap: 0 <= row < len(snap.d) and snap.d[row].strip() == want
        limit = self.tolerance(exp) + FLOAT_SLACK
        source = exp.source_channel or ""

        def shows_value(snap: Snapshot) -> bool:
            if source not in snap.s:
                return False
            actual = snap.s[source]
            return any(
                abs(float(n) - actual) <= limit
                for row in snap.d
                for n in NUMBER_PATTERN.findall(row)
            )

        return shows_value

    def window(self, exp: m.Expectation, at: int) -> list[Snapshot]:
        if exp.within_ms == 0:
            for snap in self.snapshots:
                if snap.t >= at:
                    return [snap]
            return []
        return [s for s in self.snapshots if at <= s.t <= at + exp.within_ms]

    def holds(self, check: Callable[[Snapshot], bool], first: Snapshot, hold_ms: int) -> bool:
        if not self.snapshots or self.snapshots[-1].t < first.t + hold_ms:
            return False
        return all(check(s) for s in self.snapshots if first.t <= s.t <= first.t + hold_ms)

    def satisfied(self, exp: m.Expectation, at: int) -> bool:
        check = self.predicate(exp)
        for snap in self.window(exp, at):
            if check(snap) and (exp.hold_ms == 0 or self.holds(check, snap, exp.hold_ms)):
                return True
        return False

    def hold_outcome(self, exp: m.Expectation, at: int) -> str | None:
        """For a failed hold: how it failed; None when the predicate never held at all."""
        if exp.hold_ms == 0:
            return None
        check = self.predicate(exp)
        hits = [s for s in self.window(exp, at) if check(s)]
        if not hits:
            return None
        last_t = self.snapshots[-1].t
        if all(s.t + exp.hold_ms > last_t for s in hits):
            return "did not hold; case ended before the hold time"
        return "did not hold"

    def has_snapshots(self, exp: m.Expectation, at: int) -> bool:
        return bool(self.window(exp, at))

    # --- wording ---

    def message(self, exp: m.Expectation, at: int) -> str:
        if exp.op == "eq":
            what = f"expected {exp.channel} == {_fmt(exp.value)}"
        elif exp.op == "display_contains":
            what = f'expected display to contain "{exp.value}"'
        elif exp.op == "display_row_eq":
            what = f'expected display row {exp.row} to be "{exp.value}"'
        else:
            what = (
                f"expected display to show a number within {_fmt(self.tolerance(exp))} "
                f"of {exp.source_channel}"
            )
        when = f" within {exp.within_ms} ms after t={at}" if exp.within_ms else f" at t={at}"
        hold = f" and stay for {exp.hold_ms} ms" if exp.hold_ms else ""
        return what + when + hold

    def summary(self, exp: m.Expectation, at: int) -> str:
        end = at + exp.within_ms + exp.hold_ms + SUMMARY_MARGIN_MS
        label = exp.channel
        span = f"t={at}..{end}"
        around = [s for s in self.snapshots if at <= s.t <= end]
        if not around:
            return f"{label}: no snapshots in {span}"
        if exp.op == "eq":
            body = self.run_length([_fmt(s.a.get(exp.channel, "?")) for s in around])
        else:
            body = self.run_length([self.display_text(exp, s) for s in around])
        text = f"{label}: {span} → {body}"
        if len(text) > SUMMARY_MAX_CHARS:
            text = text[: SUMMARY_MAX_CHARS - 1] + "…"
        return text

    @staticmethod
    def display_text(exp: m.Expectation, snap: Snapshot) -> str:
        text = " | ".join(row.strip() for row in snap.d if row.strip())
        if exp.op == "display_contains_value" and exp.source_channel:
            return f'"{text}" ({exp.source_channel}={_fmt(snap.s.get(exp.source_channel, "?"))})'
        return f'"{text}"'

    @staticmethod
    def run_length(items: list[str]) -> str:
        groups: list[tuple[str, int]] = []
        for item in items:
            if groups and groups[-1][0] == item:
                groups[-1] = (item, groups[-1][1] + 1)
            else:
                groups.append((item, 1))
        return ", ".join(f"{item}x{n}" for item, n in groups)


def evaluate(case: m.TestCase, run: CaseResult, registry: Registry) -> TestResult:
    """Judge a case. Runner-level failures and firmware errors replace per-expectation failures."""
    for raw in run.snapshots:
        if "fatal" in raw:
            return _error_result(case.id, f"RUNTIME_ERROR: {raw['fatal']}")
    status_error = _status_error(run)
    if status_error is not None:
        return _error_result(case.id, status_error)
    try:
        snapshots = [Snapshot.model_validate(raw) for raw in run.snapshots]
    except ValidationError:
        return _error_result(case.id, "RUNTIME_ERROR: malformed output")
    for snap in snapshots:
        if snap.err:
            return _error_result(case.id, f"RUNTIME_ERROR: {snap.err[0]}")

    judge = _Judge(snapshots, registry)
    failures: list[Failure] = []
    for index, step in enumerate(case.scenario.steps):
        for exp in step.expect:
            if judge.satisfied(exp, step.at_ms):
                continue
            message = judge.message(exp, step.at_ms)
            if not judge.has_snapshots(exp, step.at_ms):
                message = f"no snapshots recorded for this window; {message}"
            else:
                outcome = judge.hold_outcome(exp, step.at_ms)
                if outcome is not None:
                    message = f"{message}: {outcome}"
            failures.append(
                Failure(
                    step_index=index,
                    expectation=exp,
                    message=message,
                    observed_summary=judge.summary(exp, step.at_ms),
                )
            )
    return TestResult(test_id=case.id, passed=not failures, failures=failures)
