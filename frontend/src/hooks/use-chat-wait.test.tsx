import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatWait } from "@/hooks/use-chat-wait";
import { EventsProvider } from "@/providers/events-provider";
import { FakeWebSocket } from "../../tests/fake-websocket";

const TS = "2026-01-01T00:00:00Z";

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
}

const waiting = (runId: string, taskId: string, sessionId = "s1") => ({
  type: "run.llm_waiting",
  ts: TS,
  run_id: runId,
  session_id: sessionId,
  task_id: taskId,
  submit_tool: "submit_spec",
});
const answered = (runId: string, taskId: string) => ({
  type: "run.llm_answered",
  ts: TS,
  run_id: runId,
  session_id: "s1",
  task_id: taskId,
});

beforeEach(() => {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});
afterEach(() => vi.unstubAllGlobals());

describe("useChatWait", () => {
  it("is idle until a task waits, then waiting, then answered", () => {
    const { result } = renderHook(() => useChatWait("s1", "r1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    expect(result.current).toBe("idle");
    act(() => FakeWebSocket.last.receive(waiting("r1", "t1")));
    expect(result.current).toBe("waiting");
    act(() => FakeWebSocket.last.receive(answered("r1", "t1")));
    expect(result.current).toBe("answered");
    act(() => FakeWebSocket.last.receive(waiting("r1", "t2")));
    expect(result.current).toBe("waiting");
  });

  it("ignores other runs and other sessions", () => {
    const { result } = renderHook(() => useChatWait("s1", "r1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    act(() => {
      FakeWebSocket.last.receive(waiting("r2", "t1"));
      FakeWebSocket.last.receive(waiting("r1", "t2", "s2"));
    });
    expect(result.current).toBe("idle");
  });

  it("stops waiting when the run finishes", () => {
    const { result } = renderHook(() => useChatWait("s1", "r1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.receive(waiting("r1", "t1")));
    act(() =>
      FakeWebSocket.last.receive({
        type: "run.finished",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        status: "cancelled",
        failure: null,
        version: null,
        tests_passed: null,
        tests_total: null,
      })
    );
    expect(result.current).toBe("idle");
  });

  describe("seeded from the loaded run", () => {
    const render1 = (seed: string | null) =>
      renderHook(
        ({ seed }: { seed: string | null }) => useChatWait("s1", "r1", seed),
        { wrapper: Wrapper, initialProps: { seed } }
      );

    it("waits for the seeded task with no events", () => {
      const { result } = render1("t1");
      act(() => FakeWebSocket.last.open());
      expect(result.current).toBe("waiting");
    });

    it("is idle with no seed and no events", () => {
      const { result } = render1(null);
      act(() => FakeWebSocket.last.open());
      expect(result.current).toBe("idle");
    });

    it("clears a seeded wait when its task is answered", () => {
      const { result } = render1("t1");
      act(() => FakeWebSocket.last.open());
      act(() => FakeWebSocket.last.receive(answered("r1", "t1")));
      expect(result.current).toBe("answered");
    });

    it("clears a seeded wait when the run finishes", () => {
      const { result } = render1("t1");
      act(() => FakeWebSocket.last.open());
      act(() =>
        FakeWebSocket.last.receive({
          type: "run.finished",
          ts: TS,
          run_id: "r1",
          session_id: "s1",
          status: "cancelled",
          failure: null,
          version: null,
          tests_passed: null,
          tests_total: null,
        })
      );
      expect(result.current).toBe("idle");
    });

    it("clears when the run is refetched without a pending task", () => {
      const { result, rerender } = render1("t1");
      act(() => FakeWebSocket.last.open());
      expect(result.current).toBe("waiting");
      rerender({ seed: null });
      expect(result.current).toBe("idle");
    });

    it("keeps a newer live wait after the seed goes away", () => {
      const { result, rerender } = render1("t1");
      act(() => FakeWebSocket.last.open());
      act(() => FakeWebSocket.last.receive(waiting("r1", "t2")));
      rerender({ seed: null });
      expect(result.current).toBe("waiting");
    });
  });
});
