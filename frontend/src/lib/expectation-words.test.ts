import { describe, expect, it } from "vitest";

import { describeExpectation } from "@/lib/expectation-words";
import type { components } from "@/types/generated/api";

type Expectation = components["schemas"]["Expectation"];

const base: Expectation = {
  channel: "fan",
  op: "eq",
  value: true,
  hold_ms: 0,
  tolerance: 0,
  within_ms: 0,
};

const words = (patch: Partial<Expectation>) =>
  describeExpectation({ ...base, ...patch });

describe("describeExpectation", () => {
  it("words eq as is", () => {
    expect(words({})).toBe("fan is true");
  });

  it("words display_contains as contains, with the row", () => {
    expect(
      words({
        channel: "display",
        op: "display_contains",
        value: "HOT",
        row: 1,
      })
    ).toBe("display row 1 contains HOT");
  });

  it("words display_row_eq as is, with a row of 0", () => {
    expect(
      words({ channel: "display", op: "display_row_eq", value: "A", row: 0 })
    ).toBe("display row 0 is A");
  });

  it("uses the source channel when there is no value", () => {
    expect(words({ source_channel: "temp", value: null })).toBe("fan is temp");
  });

  it("adds within, for and tolerance in that order", () => {
    expect(
      words({
        channel: "temp",
        value: 25,
        within_ms: 500,
        hold_ms: 200,
        tolerance: 0.5,
      })
    ).toBe("temp is 25 within 500 ms for 200 ms ±0.5");
  });

  it("words display_contains_value as contains", () => {
    expect(
      words({
        channel: "display",
        op: "display_contains_value",
        value: null,
        source_channel: "temp",
      })
    ).toBe("display contains temp");
  });
});
