import { describe, expect, it } from "vitest";

import { relativeTime } from "@/lib/time";

const NOW = Date.parse("2026-03-10T12:00:00Z");
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const SEC = 1000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("relativeTime", () => {
  it("says just now under a minute, including small future skew", () => {
    expect(relativeTime(ago(5 * SEC), NOW)).toBe("just now");
    expect(relativeTime(ago(-5 * SEC), NOW)).toBe("just now");
    expect(relativeTime(ago(-59 * SEC), NOW)).toBe("just now");
  });

  it("formats minutes, hours and days", () => {
    expect(relativeTime(ago(3 * MIN), NOW)).toBe("3 minutes ago");
    expect(relativeTime(ago(2 * HOUR), NOW)).toBe("2 hours ago");
    expect(relativeTime(ago(DAY), NOW)).toBe("yesterday");
    expect(relativeTime(ago(5 * DAY), NOW)).toBe("5 days ago");
  });

  it("formats weeks, months and years", () => {
    expect(relativeTime(ago(14 * DAY), NOW)).toBe("2 weeks ago");
    expect(relativeTime(ago(90 * DAY), NOW)).toBe("3 months ago");
    expect(relativeTime(ago(800 * DAY), NOW)).toBe("2 years ago");
  });

  it("formats the future", () => {
    expect(relativeTime(ago(-3 * HOUR), NOW)).toBe("in 3 hours");
  });

  it("returns the input for an unparseable timestamp", () => {
    expect(relativeTime("nope", NOW)).toBe("nope");
  });
});
