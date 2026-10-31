import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunLog } from "@/components/run/run-log";

const TS = "2026-01-01T00:00:00Z";

describe("RunLog", () => {
  it("is a labelled polite log with an empty state", () => {
    render(<RunLog lines={[]} />);
    const log = screen.getByRole("log", { name: "Run log" });
    expect(log).toHaveAttribute("aria-live", "polite");
    expect(log).toHaveTextContent("Lines appear here while the run works.");
  });

  it("prefixes warn and error lines in text", () => {
    render(
      <RunLog
        lines={[
          { level: "info", message: "started", ts: TS, id: 1 },
          { level: "warn", message: "slow", ts: TS, id: 2 },
          { level: "error", message: "boom", ts: TS, id: 3 },
        ]}
      />
    );
    const log = screen.getByRole("log");
    expect(log).toHaveTextContent("started");
    expect(log).toHaveTextContent("warn: slow");
    expect(log).toHaveTextContent("error: boom");
    expect(log).not.toHaveTextContent("info:");
  });

  it("follows new lines unless the user scrolled up", () => {
    const line = (m: string) => ({
      level: "info" as const,
      message: m,
      ts: TS,
      id: m.charCodeAt(0),
    });
    const { rerender } = render(<RunLog lines={[line("a")]} />);
    const log = screen.getByRole("log");
    Object.defineProperty(log, "scrollHeight", {
      value: 1000,
      configurable: true,
    });
    Object.defineProperty(log, "clientHeight", {
      value: 100,
      configurable: true,
    });
    rerender(<RunLog lines={[line("a"), line("b")]} />);
    expect(log.scrollTop).toBe(1000);

    log.scrollTop = 200;
    log.dispatchEvent(new Event("scroll"));
    rerender(<RunLog lines={[line("a"), line("b"), line("c")]} />);
    expect(log.scrollTop).toBe(200);
  });
});
