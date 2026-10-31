import { describe, expect, it } from "vitest";

import { runSteps, type RunStep } from "@/lib/run-steps";
import type { Run } from "@/services/runs.service";

const attempt = (over: Partial<Run["attempts"][number]> = {}) => ({
  id: "a1",
  number: 1,
  source: "agent",
  stage_reached: "generated",
  safety_ok: null,
  compile_ok: null,
  tests_passed: null,
  tests_total: null,
  duration_ms: null,
  ...over,
});

const run = (over: Partial<Run> = {}): Run =>
  ({
    id: "r1",
    session_id: "s1",
    kind: "request",
    status: "pending",
    attempts: [],
    best_attempt_id: null,
    confirmed_spec: null,
    result_version: null,
    tests_total: null,
    ...over,
  }) as Run;

const spec = {
  summary: "x",
  scenarios: [],
} as unknown as Run["confirmed_spec"];

const states = (steps: RunStep[]) => steps.map((s) => s.state);
const labels = (steps: RunStep[]) => steps.map((s) => s.label);

describe("runSteps kinds", () => {
  it("lists the steps of a request run", () => {
    expect(labels(runSteps(run(), "real"))).toEqual([
      "Understand",
      "Confirm spec",
      "Generate tests",
      "Write code",
      "Check",
      "Native tests",
      "Build",
      "Flash",
      "Verify",
    ]);
  });

  it("starts a retry at Generate tests", () => {
    expect(labels(runSteps(run({ kind: "retry" }), "real"))[0]).toBe(
      "Generate tests"
    );
    expect(runSteps(run({ kind: "retry" }), "real")).toHaveLength(7);
  });

  it("lists the steps of manual and rollback runs", () => {
    expect(labels(runSteps(run({ kind: "manual" }), "real"))).toEqual([
      "Check",
      "Native tests",
      "Build",
      "Flash",
      "Verify",
    ]);
    expect(labels(runSteps(run({ kind: "rollback" }), "real"))).toEqual([
      "Flash",
      "Verify",
    ]);
  });

  it("uses virtual labels and a note on Build", () => {
    const steps = runSteps(run({ kind: "manual" }), "virtual");
    expect(labels(steps)).toContain("Load into virtual board");
    expect(labels(steps)).not.toContain("Flash");
    expect(steps.find((s) => s.key === "build")?.note).toBe(
      "skipped in virtual"
    );
    const real = runSteps(run({ kind: "manual" }), "real");
    expect(real.find((s) => s.key === "build")?.note).toBeUndefined();
  });
});

describe("runSteps working and waiting", () => {
  it("marks the step of a working status current", () => {
    const steps = runSteps(run({ status: "coding" }), "real");
    expect(states(steps)).toEqual([
      "done",
      "done",
      "done",
      "current",
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("makes the first step current while pending", () => {
    expect(states(runSteps(run({ status: "pending" }), "real"))[0]).toBe(
      "current"
    );
    expect(
      states(runSteps(run({ kind: "retry", status: "pending" }), "real"))[0]
    ).toBe("current");
  });

  it("marks the waiting steps", () => {
    const answer = runSteps(run({ status: "awaiting_answer" }), "real");
    expect(states(answer).slice(0, 2)).toEqual(["waiting", "pending"]);
    const confirm = runSteps(run({ status: "awaiting_confirmation" }), "real");
    expect(states(confirm).slice(0, 3)).toEqual(["done", "waiting", "pending"]);
  });

  it("handles the verifying status of a rollback", () => {
    expect(
      states(runSteps(run({ kind: "rollback", status: "verifying" }), "real"))
    ).toEqual(["done", "current"]);
  });
});

describe("runSteps finished", () => {
  it("marks every step done when the run succeeded", () => {
    const steps = runSteps(
      run({
        status: "succeeded",
        result_version: { id: "v", number: 1, status: "verified" },
      }),
      "real"
    );
    expect(new Set(states(steps))).toEqual(new Set(["done"]));
  });

  it("fails at Understand with no evidence", () => {
    const steps = runSteps(run({ status: "failed" }), "real");
    expect(states(steps)[0]).toBe("failed");
    expect(new Set(states(steps).slice(1))).toEqual(new Set(["pending"]));
  });

  it("fails at Generate tests once the spec is confirmed", () => {
    const steps = runSteps(
      run({ status: "failed", confirmed_spec: spec }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("tests");
    expect(states(steps).slice(0, 2)).toEqual(["done", "done"]);
  });

  it("fails at Write code once tests exist", () => {
    const steps = runSteps(
      run({ status: "failed", confirmed_spec: spec, tests_total: 5 }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("code");
  });

  it("picks Check or Native tests from the latest attempt stage", () => {
    const base = { status: "failed", confirmed_spec: spec, tests_total: 5 };
    const at = (stage: string) =>
      runSteps(
        run({ ...base, attempts: [attempt({ stage_reached: stage })] }),
        "real"
      ).find((s) => s.state === "failed")?.key;
    expect(at("generated")).toBe("check");
    expect(at("safety")).toBe("check");
    expect(at("compile")).toBe("check");
    expect(at("tests")).toBe("native");
  });

  it("uses the latest attempt, not the first", () => {
    const steps = runSteps(
      run({
        status: "failed",
        confirmed_spec: spec,
        tests_total: 5,
        attempts: [
          attempt({ id: "a1", number: 1, stage_reached: "tests" }),
          attempt({ id: "a2", number: 2, stage_reached: "safety" }),
        ],
      }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("check");
  });

  it("fails at Build when the best attempt passed every test", () => {
    const steps = runSteps(
      run({
        status: "failed",
        confirmed_spec: spec,
        tests_total: 5,
        best_attempt_id: "a1",
        attempts: [
          attempt({ stage_reached: "tests", tests_passed: 5, tests_total: 5 }),
        ],
      }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("build");
    expect(steps.find((s) => s.key === "native")?.state).toBe("done");
  });

  it("does not treat a best attempt with failing tests as passed", () => {
    const steps = runSteps(
      run({
        status: "failed",
        confirmed_spec: spec,
        tests_total: 5,
        best_attempt_id: "a1",
        attempts: [
          attempt({ stage_reached: "tests", tests_passed: 4, tests_total: 5 }),
        ],
      }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("native");
  });

  it("marks everything done when a failed run has a result version", () => {
    const steps = runSteps(
      run({
        status: "failed",
        result_version: { id: "v", number: 2, status: "verified" },
      }),
      "real"
    );
    expect(new Set(states(steps))).toEqual(new Set(["done"]));
  });

  it("clamps the failed step into the steps of the run kind", () => {
    const manual = runSteps(
      run({
        kind: "manual",
        status: "failed",
        confirmed_spec: spec,
        tests_total: 5,
      }),
      "real"
    );
    expect(manual.find((s) => s.state === "failed")?.key).toBe("check");
    const rollback = runSteps(
      run({ kind: "rollback", status: "failed" }),
      "real"
    );
    expect(states(rollback)).toEqual(["failed", "pending"]);
  });

  it("places a cancelled run like a failed one", () => {
    const steps = runSteps(
      run({ status: "cancelled", confirmed_spec: spec }),
      "real"
    );
    expect(steps.find((s) => s.state === "failed")?.key).toBe("tests");
  });
});
