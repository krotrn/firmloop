import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunStatusBadge } from "@/components/runs/run-status-badge";

const cases: Array<[string, string]> = [
  ["pending", "Queued"],
  ["understanding", "Understanding"],
  ["awaiting_answer", "Needs you"],
  ["awaiting_confirmation", "Needs you"],
  ["generating_tests", "Writing tests"],
  ["coding", "Coding"],
  ["checking", "Checking"],
  ["native_testing", "Testing"],
  ["building", "Building"],
  ["flashing", "Flashing"],
  ["verifying", "Verifying"],
  ["succeeded", "Succeeded"],
  ["failed", "Failed"],
  ["cancelled", "Cancelled"],
];

describe("RunStatusBadge", () => {
  it.each(cases)("labels %s as %s", (status, label) => {
    render(<RunStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("shows unknown statuses as raw text", () => {
    render(<RunStatusBadge status="mystery" />);
    expect(screen.getByText("mystery")).toBeInTheDocument();
  });

  it("marks working statuses with the pulse and terminal ones without", () => {
    const { rerender } = render(<RunStatusBadge status="coding" />);
    expect(screen.getByText("Coding").className).toContain(
      "motion-safe:animate-pulse"
    );
    rerender(<RunStatusBadge status="succeeded" />);
    expect(screen.getByText("Succeeded").className).not.toContain(
      "animate-pulse"
    );
  });

  it("styles failures as destructive and waiting as distinct", () => {
    const { rerender } = render(<RunStatusBadge status="failed" />);
    expect(screen.getByText("Failed").className).toContain("text-destructive");
    rerender(<RunStatusBadge status="awaiting_answer" />);
    expect(screen.getByText("Needs you").className).toContain("amber");
  });
});

describe("RunStatusBadge styling groups", () => {
  it("uses the positive style for succeeded and secondary for cancelled", () => {
    const { rerender } = render(<RunStatusBadge status="succeeded" />);
    expect(screen.getByText("Succeeded").className).toContain("bg-primary");
    rerender(<RunStatusBadge status="cancelled" />);
    expect(screen.getByText("Cancelled").className).toContain("bg-secondary");
  });

  it("uses the outline style for every working status", () => {
    for (const [status, label] of [
      ["pending", "Queued"],
      ["understanding", "Understanding"],
      ["generating_tests", "Writing tests"],
      ["coding", "Coding"],
      ["checking", "Checking"],
      ["native_testing", "Testing"],
      ["building", "Building"],
      ["flashing", "Flashing"],
      ["verifying", "Verifying"],
    ]) {
      const { unmount } = render(<RunStatusBadge status={status} />);
      const el = screen.getByText(label);
      expect(el.className).toContain("border-border");
      expect(el.className).toContain("motion-safe:animate-pulse");
      unmount();
    }
  });

  it("treats prototype keys as unknown statuses", () => {
    render(<RunStatusBadge status="constructor" />);
    const el = screen.getByText("constructor");
    expect(el.className).toContain("border-border");
    expect(el.className).not.toContain("animate-pulse");
  });
});
