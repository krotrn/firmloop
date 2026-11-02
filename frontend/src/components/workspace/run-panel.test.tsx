import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunPanel } from "@/components/workspace/run-panel";
import { EventsProvider } from "@/providers/events-provider";
import type { Run } from "@/services/runs.service";
import { FakeWebSocket } from "../../../tests/fake-websocket";

const mounts = vi.hoisted(() => ({ code: 0 }));

vi.mock("@/components/code/code-tab", async () => {
  const React = await import("react");
  return {
    CodeTab: ({ run }: { run: { id: string } }) => {
      React.useEffect(() => {
        mounts.code += 1;
      }, []);
      return <p>Code for {run.id}</p>;
    },
  };
});

vi.mock("@/components/versions/versions-tab", () => ({
  VersionsTab: ({ sessionId }: { sessionId: string }) => (
    <p>Versions for {sessionId}</p>
  ),
}));

vi.mock("@/components/tests/tests-tab", () => ({
  TestsTab: ({ run }: { run: { id: string } }) => <p>Tests for {run.id}</p>,
}));

const run = (id: string): Run =>
  ({
    id,
    session_id: "s1",
    kind: "request",
    input_mode: "text",
    request_text: `Request ${id}`,
    status: "coding",
    attempts: [],
    best_attempt_id: null,
    confirmed_spec: null,
    result_version: null,
    tests_total: null,
    failure: null,
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
  }) as unknown as Run;

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});
afterEach(() => vi.unstubAllGlobals());

describe("RunPanel Run tab", () => {
  it("shows the summary, step timeline, attempts list and log", () => {
    render(<RunPanel run={run("r1")} sessionId="s1" deviceMode="real" />, {
      wrapper: Wrapper,
    });
    expect(screen.getByText("Request r1")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Attempts" })
    ).toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Run log" })).toBeInTheDocument();
  });

  it("shows live log lines for the selected run only and resets the log box per run", () => {
    const { rerender } = render(
      <RunPanel run={run("r1")} sessionId="s1" deviceMode="real" />,
      { wrapper: Wrapper }
    );
    act(() => FakeWebSocket.last.open());
    act(() =>
      FakeWebSocket.last.receive({
        type: "run.log",
        ts: "2026-01-01T00:00:00Z",
        run_id: "r1",
        session_id: "s1",
        level: "info",
        message: "from r1",
      })
    );
    const logA = screen.getByRole("log");
    expect(logA).toHaveTextContent("from r1");

    rerender(<RunPanel run={run("r2")} sessionId="s1" deviceMode="real" />);
    const logB = screen.getByRole("log");
    expect(logB).not.toBe(logA);
    expect(logB).not.toHaveTextContent("from r1");
  });
});

describe("RunPanel chat step", () => {
  it("shows Waiting for chat from the events and drops it when the run ends", () => {
    const { rerender } = render(
      <RunPanel run={run("r1")} sessionId="s1" deviceMode="real" />,
      { wrapper: Wrapper }
    );
    act(() => FakeWebSocket.last.open());
    expect(screen.queryByText("Waiting for chat")).not.toBeInTheDocument();
    act(() =>
      FakeWebSocket.last.receive({
        type: "run.llm_waiting",
        ts: "2026-01-01T00:00:00Z",
        run_id: "r1",
        session_id: "s1",
        task_id: "t1",
        submit_tool: "submit_spec",
      })
    );
    expect(screen.getByText("Waiting for chat")).toBeInTheDocument();
    rerender(
      <RunPanel
        run={{ ...run("r1"), status: "cancelled" }}
        sessionId="s1"
        deviceMode="real"
      />
    );
    expect(screen.queryByText("Waiting for chat")).not.toBeInTheDocument();
  });
});

describe("RunPanel Code and Tests tabs", () => {
  beforeEach(() => {
    mounts.code = 0;
  });

  it("shows the Code tab for the selected run and remounts it when the run changes", async () => {
    const { rerender } = render(
      <RunPanel run={run("r1")} sessionId="s1" deviceMode="real" />,
      { wrapper: Wrapper }
    );
    await userEvent.click(screen.getByRole("tab", { name: "Code" }));
    expect(screen.getByText("Code for r1")).toBeInTheDocument();
    expect(mounts.code).toBe(1);

    rerender(<RunPanel run={run("r2")} sessionId="s1" deviceMode="real" />);
    expect(screen.getByText("Code for r2")).toBeInTheDocument();
    expect(mounts.code).toBe(2);
  });

  it("shows the Tests tab for the selected run", async () => {
    render(<RunPanel run={run("r1")} sessionId="s1" deviceMode="real" />, {
      wrapper: Wrapper,
    });
    await userEvent.click(screen.getByRole("tab", { name: "Tests" }));
    expect(screen.getByText("Tests for r1")).toBeInTheDocument();
  });

  it("keeps the empty states when no run is selected", async () => {
    render(<RunPanel run={null} sessionId="s1" deviceMode="real" />, {
      wrapper: Wrapper,
    });
    await userEvent.click(screen.getByRole("tab", { name: "Code" }));
    expect(screen.getByText("No code yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Tests" }));
    expect(screen.getByText("No tests yet")).toBeInTheDocument();
  });
});

describe("RunPanel Versions tab", () => {
  it("shows the session's versions even without a selected run", async () => {
    render(<RunPanel run={null} sessionId="s1" deviceMode="real" />, {
      wrapper: Wrapper,
    });
    await userEvent.click(screen.getByRole("tab", { name: "Versions" }));
    expect(screen.getByText("Versions for s1")).toBeInTheDocument();
  });
});
