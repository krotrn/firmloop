import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hardwareService, type Hardware } from "@/services/hardware.service";
import { useHardware } from "./use-hardware";

const hardware: Hardware = { components: [], channels: [] };

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  function QueryWrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  return QueryWrapper;
};

describe("useHardware", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches the registry once and returns it", async () => {
    const spy = vi
      .spyOn(hardwareService, "getHardware")
      .mockResolvedValue(hardware);

    const { result, rerender } = renderHook(() => useHardware(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(hardware);
    rerender();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
