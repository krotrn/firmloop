import { describe, expect, it } from "vitest";

import {
  deepEqual,
  msToSeconds,
  secondsToMs,
  validateScenarios,
  wasEdited,
  type ChannelLike,
  type Scenario,
} from "@/lib/spec-edit";

const channels: ChannelLike[] = [
  {
    id: "temp",
    kind: "sensor",
    type: "float",
    range: [-10, 50],
    resolution: 0.1,
  },
  { id: "fan", kind: "actuator", type: "bool", range: null, resolution: null },
  {
    id: "display",
    kind: "display",
    type: "text",
    range: null,
    resolution: null,
    rows: 4,
    cols: 21,
  },
];

const scenario = (over: Partial<Scenario> = {}): Scenario => ({
  id: "s1",
  title: "Hot",
  given: { temp: 20 },
  steps: [
    { at_ms: 0, set: { temp: 30 }, expect: [] },
    {
      at_ms: 1000,
      set: {},
      expect: [
        {
          channel: "fan",
          op: "eq",
          value: true,
          hold_ms: 0,
          tolerance: 0,
          within_ms: 0,
        },
      ],
    },
  ],
  ...over,
});

describe("spec-edit", () => {
  it("converts seconds and milliseconds", () => {
    expect(msToSeconds(1500)).toBe(1.5);
    expect(secondsToMs(1.5)).toBe(1500);
    expect(secondsToMs(0.1 + 0.2)).toBe(300);
  });

  it("compares deeply", () => {
    expect(deepEqual({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
    expect(deepEqual({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(wasEdited([scenario()], [scenario()])).toBe(false);
    expect(wasEdited([scenario()], [scenario({ title: "Cold" })])).toBe(true);
  });

  it("accepts a valid scenario", () => {
    expect(validateScenarios([scenario()], channels)).toEqual([]);
  });

  it("flags non-increasing times", () => {
    const s = scenario();
    s.steps[1]!.at_ms = 0;
    expect(validateScenarios([s], channels)).toEqual([
      {
        scenarioId: "s1",
        stepIndex: 1,
        message: "The time must be later than the previous step.",
      },
    ]);
  });

  it("flags out of range values, no steps and too many steps", () => {
    const s = scenario();
    s.steps[0]!.set = { temp: 99 };
    expect(validateScenarios([s], channels)[0]?.message).toBe(
      "temp must be between -10 and 50."
    );
    expect(
      validateScenarios([scenario({ steps: [] })], channels)[0]?.message
    ).toBe("Add at least one step.");
    const many = Array.from({ length: 21 }, (_, i) => ({
      at_ms: i,
      expect: [],
    }));
    expect(
      validateScenarios([scenario({ steps: many })], channels).map(
        (i) => i.message
      )
    ).toContain("Use at most 20 steps.");
  });

  it("flags missing required fields", () => {
    const s = scenario({ title: " " });
    s.steps[1]!.expect = [
      {
        channel: "display",
        op: "display_row_eq",
        value: "",
        row: null,
        hold_ms: 0,
        tolerance: 0,
        within_ms: 0,
      },
    ];
    const messages = validateScenarios([s], channels).map((i) => i.message);
    expect(messages).toContain("The title is required.");
    expect(messages).toContain("Enter the text the display row should show.");
    expect(messages).toContain("Choose a valid display row.");
  });

  it("rounds seconds to ms without float drift", () => {
    expect(secondsToMs(0.1)).toBe(100);
    expect(secondsToMs(1.15)).toBe(1150);
  });

  it("rejects fractions on int channels", () => {
    const withInt: ChannelLike[] = [
      ...channels,
      {
        id: "count",
        kind: "sensor",
        type: "int",
        range: [0, 10],
        resolution: 1,
      },
    ];
    const s = scenario();
    s.steps[0]!.set = { count: 2.5 };
    expect(validateScenarios([s], withInt)[0]?.message).toBe(
      "count must be a whole number."
    );
  });

  it("flags scenarios longer than 120 seconds", () => {
    const s = scenario();
    s.steps[1]!.expect[0]!.within_ms = 119_900;
    expect(validateScenarios([s], channels).map((i) => i.message)).toContain(
      "The scenario is longer than 120 seconds."
    );
    s.steps[1]!.expect[0]!.within_ms = 118_000;
    expect(validateScenarios([s], channels)).toEqual([]);
  });

  it("checks channel kinds per section", () => {
    const s = scenario();
    s.steps[0]!.set = { fan: true };
    s.steps[1]!.expect[0]!.channel = "temp";
    const messages = validateScenarios([s], channels).map((i) => i.message);
    expect(messages).toContain("fan is not a sensor, so it cannot be set.");
    expect(messages).toContain(
      "temp is a sensor; expect an output or the display."
    );
  });

  it("skips channel lookups when no channels are known", () => {
    expect(validateScenarios([scenario()], [])).toEqual([]);
  });
});
