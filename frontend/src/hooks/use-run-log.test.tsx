import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { useRunLog } from "@/hooks/use-run-log";
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

const log = (
  runId: string,
  message: string,
  over: Record<string, unknown> = {}
) => ({
  type: "run.log",
  ts: TS,
  run_id: runId,
  session_id: "s1",
  level: "info",
  message,
  ...over,
});

beforeEach(() => {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});
afterEach(() => vi.unstubAllGlobals());

describe("useRunLog", () => {
  it("collects log lines of the selected run", () => {
    const { result } = renderHook(() => useRunLog("s1", "r1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    expect(result.current).toEqual([]);
    act(() => {
      FakeWebSocket.last.receive(log("r1", "one"));
      FakeWebSocket.last.receive(log("r1", "two", { level: "warn" }));
      FakeWebSocket.last.receive(log("r2", "other run"));
      FakeWebSocket.last.receive(
        log("r1", "other session", { session_id: "s2" })
      );
    });
    expect(result.current).toMatchObject([
      { level: "info", message: "one", ts: TS },
      { level: "warn", message: "two", ts: TS },
    ]);
    const [first, second] = result.current;
    expect(second!.id).toBeGreaterThan(first!.id);
  });

  it("keeps lines of a run while another run is selected", () => {
    const { result, rerender } = renderHook(
      ({ runId }) => useRunLog("s1", runId),
      { wrapper: Wrapper, initialProps: { runId: "r1" } }
    );
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.receive(log("r1", "first")));
    rerender({ runId: "r2" });
    expect(result.current).toEqual([]);
    act(() => FakeWebSocket.last.receive(log("r1", "while away")));
    act(() => FakeWebSocket.last.receive(log("r2", "second")));
    expect(result.current.map((l) => l.message)).toEqual(["second"]);
    rerender({ runId: "r1" });
    expect(result.current.map((l) => l.message)).toEqual([
      "first",
      "while away",
    ]);
  });

  it("drops the oldest lines beyond 500", () => {
    const { result } = renderHook(() => useRunLog("s1", "r1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.open());
    act(() => {
      for (let i = 0; i < 510; i++)
        FakeWebSocket.last.receive(log("r1", `m${i}`));
    });
    expect(result.current).toHaveLength(500);
    expect(result.current[0]?.message).toBe("m10");
    expect(result.current.at(-1)?.message).toBe("m509");
    expect(new Set(result.current.map((l) => l.id)).size).toBe(500);
  });

  it("returns nothing without a run", () => {
    const { result } = renderHook(() => useRunLog("s1", null), {
      wrapper: Wrapper,
    });
    expect(result.current).toEqual([]);
  });
});
