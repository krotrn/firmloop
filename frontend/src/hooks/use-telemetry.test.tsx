import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTelemetry } from "@/hooks/use-telemetry";
import { EventsProvider } from "@/providers/events-provider";
import type { ServerEvent } from "@/types/generated/events";
import { FakeWebSocket } from "../../tests/fake-websocket";

const TS = "2026-01-01T00:00:00Z";

const telemetry = (t: number): ServerEvent => ({
  type: "device.telemetry",
  ts: TS,
  mode: "run",
  t,
  s: { temp: 20 },
  a: {},
  d: ["", "", "", ""],
});

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
}

describe("useTelemetry", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is null until a telemetry event arrives, then follows the latest", () => {
    const { result } = renderHook(() => useTelemetry(), { wrapper: Wrapper });
    expect(result.current).toBeNull();
    const socket = FakeWebSocket.last;
    act(() => {
      socket.open();
      socket.receive(telemetry(1));
    });
    expect(result.current?.t).toBe(1);
    act(() => {
      socket.receive({
        type: "run.status",
        ts: TS,
        run_id: "r",
        session_id: "s",
        status: "testing",
        previous_status: null,
      });
      socket.receive(telemetry(2));
    });
    expect(result.current?.t).toBe(2);
  });
});
