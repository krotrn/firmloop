import type { components } from "@/types/generated/api";

type Expectation = components["schemas"]["Expectation"];

/** An expectation in words, e.g. "fan is true within 500 ms". */
export function describeExpectation(e: Expectation): string {
  // The channel already names the display, so display ops read as plain words.
  const op = e.op === "eq" || e.op === "display_row_eq" ? "is" : "contains";
  const parts = [
    e.channel,
    e.row === null || e.row === undefined ? null : `row ${e.row}`,
    op,
    String(e.value ?? e.source_channel ?? ""),
    e.within_ms > 0 ? `within ${e.within_ms} ms` : null,
    e.hold_ms > 0 ? `for ${e.hold_ms} ms` : null,
    e.tolerance > 0 ? `±${e.tolerance}` : null,
  ];
  return parts.filter((part) => part !== null && part !== "").join(" ");
}
