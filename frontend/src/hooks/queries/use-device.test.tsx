import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deviceQueryKeys, useDevice } from "@/hooks/queries/use-device";
import { EventsProvider } from "@/providers/events-provider";
import { deviceService } from "@/services/device.service";
import { FakeWebSocket } from "../../../tests/fake-websocket";

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } })
  );
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

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useDevice", () => {
  it("exposes the key", () => {
    expect(deviceQueryKeys.status).toEqual(["device"]);
  });

  it("loads the device and replaces it on a device.status event", async () => {
    vi.spyOn(deviceService, "getDevice").mockResolvedValue({
      mode: "virtual",
      connected: false,
      busy: false,
    });
    const { result } = renderHook(() => useDevice(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.data?.connected).toBe(false));

    act(() => {
      FakeWebSocket.last.receive({
        type: "device.status",
        ts: "2026-01-01T00:00:00Z",
        status: { mode: "real", connected: true, port: "/dev/ttyUSB0" },
      });
    });
    await waitFor(() => expect(result.current.data?.mode).toBe("real"));
    expect(result.current.data).toMatchObject({
      connected: true,
      port: "/dev/ttyUSB0",
      busy: false,
    });
  });
});
