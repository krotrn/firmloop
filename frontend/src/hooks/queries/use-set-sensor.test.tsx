import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSetSensor } from "@/hooks/queries/use-set-sensor";
import { deviceCommandService } from "@/services/device-command.service";

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(() => new QueryClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useSetSensor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the channel and value", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useSetSensor(), { wrapper: Wrapper });
    act(() => {
      result.current.mutate({ channel: "temp", value: 30 });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(setSensor).toHaveBeenCalledWith("temp", 30);
  });

  it("exposes the error", async () => {
    vi.spyOn(deviceCommandService, "setSensor").mockRejectedValue(
      new Error("busy")
    );
    const { result } = renderHook(() => useSetSensor(), { wrapper: Wrapper });
    act(() => {
      result.current.mutate({ channel: "motion", value: true });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("busy");
  });
});
