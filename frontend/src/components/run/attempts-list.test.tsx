import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AttemptsList } from "@/components/run/attempts-list";
import type { Run } from "@/services/runs.service";

const run = (over: Partial<Run> = {}): Run =>
  ({ id: "r1", attempts: [], best_attempt_id: null, ...over }) as Run;

describe("AttemptsList", () => {
  it("shows an empty state", () => {
    render(<AttemptsList run={run()} />);
    expect(screen.getByText(/no attempts yet/i)).toBeInTheDocument();
  });

  it("lists attempts and marks the best one", () => {
    render(
      <AttemptsList
        run={run({
          best_attempt_id: "a2",
          attempts: [
            {
              id: "a1",
              number: 1,
              source: "agent",
              stage_reached: "compile",
              safety_ok: true,
              compile_ok: false,
              tests_passed: null,
              tests_total: null,
              duration_ms: 800,
            },
            {
              id: "a2",
              number: 2,
              source: "agent",
              stage_reached: "tests",
              safety_ok: true,
              compile_ok: true,
              tests_passed: 4,
              tests_total: 5,
              duration_ms: 2500,
            },
          ],
        })}
      />
    );
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    const first = within(rows[1]!);
    expect(first.getByText("#1")).toBeInTheDocument();
    expect(first.getByText("Failed")).toBeInTheDocument();
    expect(first.getByText("800 ms")).toBeInTheDocument();
    const second = within(rows[2]!);
    expect(second.getByText("Best")).toBeInTheDocument();
    expect(second.getByText("4/5")).toBeInTheDocument();
    expect(second.getByText("2.5 s")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Attempts table" })
    ).toHaveAttribute("tabindex", "0");
  });

  it("words each stage from what happened", () => {
    const at = (stage_reached: string, safety_ok: boolean | null) => ({
      id: stage_reached + String(safety_ok),
      number: 1,
      source: "agent",
      stage_reached,
      safety_ok,
      compile_ok: null,
      tests_passed: null,
      tests_total: null,
      duration_ms: null,
    });
    render(
      <AttemptsList
        run={run({
          attempts: [
            at("generated", null),
            at("safety", true),
            at("safety", false),
            at("compile", true),
            at("tests", true),
          ],
        })}
      />
    );
    for (const text of [
      "Code written",
      "Safety checked",
      "Safety failed",
      "Compile failed",
      "Tests run",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.queryByText("Compiled")).not.toBeInTheDocument();
  });
});
