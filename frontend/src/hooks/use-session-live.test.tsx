import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionLive } from "@/hooks/use-session-live";
import { EventsProvider } from "@/providers/events-provider";
import { FakeWebSocket } from "../../tests/fake-websocket";

const TS = "2026-01-01T00:00:00Z";

let queryClient: QueryClient;
let invalidate: ReturnType<typeof vi.spyOn>;

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
}

const runStatus = (sessionId: string) => ({
  type: "run.status",
  ts: TS,
  run_id: "r1",
  session_id: sessionId,
  status: "coding",
  previous_status: null,
});

beforeEach(() => {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  queryClient = new QueryClient();
  invalidate = vi.spyOn(queryClient, "invalidateQueries");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useSessionLive", () => {
  it("subscribes to the session and returns its run events", () => {
    const { result } = renderHook(() => useSessionLive("s1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    expect(FakeWebSocket.last.sentMessages).toContainEqual({
      type: "subscribe",
      session_id: "s1",
    });
    act(() => FakeWebSocket.last.receive(runStatus("s1")));
    expect(result.current.events.map((e) => e.type)).toEqual(["run.status"]);
  });

  it("invalidates the session queries for its status-changing events", () => {
    renderHook(() => useSessionLive("s1"), { wrapper: Wrapper });
    const socket = FakeWebSocket.last;
    act(() => socket.open());
    invalidate.mockClear();

    act(() => {
      socket.receive(runStatus("s1"));
      socket.receive({
        type: "run.question",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        question: { question: "Which pin?", reason: "unclear" },
      });
      socket.receive({
        type: "run.finished",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        status: "succeeded",
        version: null,
        tests_passed: null,
        tests_total: null,
        failure: null,
      });
    });
    expect(invalidate.mock.calls.length).toBeGreaterThanOrEqual(2);
    for (const call of invalidate.mock.calls) {
      expect(call[0]).toEqual({ queryKey: ["sessions", "s1"] });
    }
  });

  it("invalidates for run.created when the run belongs to the session", () => {
    renderHook(() => useSessionLive("s1"), { wrapper: Wrapper });
    const socket = FakeWebSocket.last;
    act(() => socket.open());
    invalidate.mockClear();

    act(() =>
      socket.receive({ type: "run.created", ts: TS, run: { session_id: "s2" } })
    );
    expect(invalidate).not.toHaveBeenCalled();
    act(() =>
      socket.receive({ type: "run.created", ts: TS, run: { session_id: "s1" } })
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("ignores another session's events and non-status events", () => {
    renderHook(() => useSessionLive("s1"), { wrapper: Wrapper });
    const socket = FakeWebSocket.last;
    act(() => socket.open());
    invalidate.mockClear();

    act(() => {
      socket.receive(runStatus("s2"));
      socket.receive({
        type: "run.log",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        level: "info",
        message: "hi",
      });
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
