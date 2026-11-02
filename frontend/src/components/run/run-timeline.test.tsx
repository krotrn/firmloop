import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunTimeline } from "@/components/run/run-timeline";
import type { Run } from "@/services/runs.service";

const run = (over: Partial<Run> = {}): Run =>
  ({
    id: "r1",
    kind: "request",
    status: "coding",
    attempts: [],
    best_attempt_id: null,
    confirmed_spec: null,
    result_version: null,
    tests_total: null,
    ...over,
  }) as Run;

describe("RunTimeline", () => {
  it("renders an ordered list with the current step marked", () => {
    render(<RunTimeline run={run()} deviceMode="real" />);
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(9);
    const current = items.filter((li) => li.getAttribute("aria-current"));
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Write code");
    expect(current[0]).toHaveTextContent("In progress");
  });

  it("labels waiting and failed steps in text", () => {
    const { rerender } = render(
      <RunTimeline
        run={run({ status: "awaiting_confirmation" })}
        deviceMode="real"
      />
    );
    expect(screen.getByText("Waiting for you")).toBeInTheDocument();
    rerender(<RunTimeline run={run({ status: "failed" })} deviceMode="real" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    rerender(
      <RunTimeline run={run({ status: "cancelled" })} deviceMode="real" />
    );
    expect(screen.getByText("Stopped")).toBeInTheDocument();
  });

  it("shows virtual labels", () => {
    render(<RunTimeline run={run({ kind: "manual" })} deviceMode="virtual" />);
    expect(screen.getByText("Load into virtual board")).toBeInTheDocument();
    expect(screen.getByText("(skipped in virtual)")).toBeInTheDocument();
  });

  it("adds a running chat step after the current step while waiting", () => {
    render(<RunTimeline run={run()} deviceMode="real" chatWait="waiting" />);
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[3]).toHaveTextContent("Write code");
    expect(items[4]).toHaveTextContent("Waiting for chat");
    expect(items[4]).toHaveTextContent("In progress");
    expect(items[4]).toHaveAttribute("aria-current", "step");
  });

  it("shows the chat step done once answered", () => {
    render(<RunTimeline run={run()} deviceMode="real" chatWait="answered" />);
    const item = screen
      .getAllByRole("listitem")
      .find((li) => li.textContent?.includes("Waiting for chat"));
    expect(item).toHaveTextContent("Done");
    expect(item).not.toHaveAttribute("aria-current");
  });

  it("shows no chat step when idle or when the run ended while waiting", () => {
    const { rerender } = render(<RunTimeline run={run()} deviceMode="real" />);
    expect(screen.queryByText("Waiting for chat")).not.toBeInTheDocument();
    rerender(
      <RunTimeline
        run={run({ status: "cancelled" })}
        deviceMode="real"
        chatWait="waiting"
      />
    );
    expect(screen.queryByText("Waiting for chat")).not.toBeInTheDocument();
  });
});
