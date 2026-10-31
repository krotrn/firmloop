import type { DeviceStatus } from "@/services/device.service";
import type { Run } from "@/services/runs.service";

export type StepState = "done" | "current" | "waiting" | "failed" | "pending";

export interface RunStep {
  key: StepKey;
  label: string;
  state: StepState;
  note?: string;
}

type StepKey =
  | "understand"
  | "confirm"
  | "tests"
  | "code"
  | "check"
  | "native"
  | "build"
  | "flash"
  | "verify";

type DeviceMode = DeviceStatus["mode"];

const ORDER: readonly StepKey[] = [
  "understand",
  "confirm",
  "tests",
  "code",
  "check",
  "native",
  "build",
  "flash",
  "verify",
];

const LABELS: Record<StepKey, string> = {
  understand: "Understand",
  confirm: "Confirm spec",
  tests: "Generate tests",
  code: "Write code",
  check: "Check",
  native: "Native tests",
  build: "Build",
  flash: "Flash",
  verify: "Verify",
};

const KIND_START: Record<string, StepKey> = {
  request: "understand",
  retry: "tests",
  manual: "check",
  rollback: "flash",
};

const STATUS_STEP: Record<string, StepKey> = {
  understanding: "understand",
  awaiting_answer: "understand",
  awaiting_confirmation: "confirm",
  generating_tests: "tests",
  coding: "code",
  checking: "check",
  native_testing: "native",
  building: "build",
  flashing: "flash",
  verifying: "verify",
};

const WAITING = new Set(["awaiting_answer", "awaiting_confirmation"]);

const index = (key: StepKey): number => ORDER.indexOf(key);

/** Global index of the first step that has no evidence of having finished. */
function nextStepAfterEvidence(run: Run): number {
  if (run.result_version) return ORDER.length;
  let next = 0;
  if (run.confirmed_spec) next = index("tests");
  if (run.tests_total != null) next = index("code");
  const latest = run.attempts.at(-1);
  if (latest) {
    next = index(latest.stage_reached === "tests" ? "native" : "check");
  }
  const best = run.attempts.find((a) => a.id === run.best_attempt_id);
  if (
    best &&
    best.tests_total != null &&
    best.tests_total > 0 &&
    best.tests_passed === best.tests_total
  ) {
    next = index("build");
  }
  return next;
}

/** The ordered steps of a run with their state, for the step timeline. */
export function runSteps(run: Run, deviceMode: DeviceMode | null): RunStep[] {
  const start = index(KIND_START[run.kind] ?? "understand");
  const keys = ORDER.slice(start);

  let currentGlobal = -1;
  let failedGlobal = -1;
  let allDone = false;
  if (run.status === "succeeded") {
    allDone = true;
  } else if (run.status === "failed" || run.status === "cancelled") {
    const next = nextStepAfterEvidence(run);
    if (next >= ORDER.length) allDone = true;
    else failedGlobal = Math.max(next, start);
  } else if (run.status === "pending") {
    currentGlobal = start;
  } else {
    const key = STATUS_STEP[run.status];
    currentGlobal = key ? Math.max(index(key), start) : start;
  }

  return keys.map((key) => {
    const position = index(key);
    let state: StepState = "pending";
    if (allDone) state = "done";
    else if (failedGlobal >= 0) {
      state =
        position < failedGlobal
          ? "done"
          : position === failedGlobal
            ? "failed"
            : "pending";
    } else if (position < currentGlobal) state = "done";
    else if (position === currentGlobal)
      state = WAITING.has(run.status) ? "waiting" : "current";

    const step: RunStep = {
      key,
      label:
        key === "flash" && deviceMode === "virtual"
          ? "Load into virtual board"
          : LABELS[key],
      state,
    };
    if (key === "build" && deviceMode === "virtual") {
      step.note = "skipped in virtual";
    }
    return step;
  });
}
