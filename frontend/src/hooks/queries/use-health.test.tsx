import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { healthService, type Health } from "@/services/health.service";
import { useHealthQuery } from "./use-health";

const health: Health = {
  ok: true,
  services: { builder: "ok", device: "ok", llm: "ok", runner: "ok" },
  lib_version: "0.1.0",
  proto: 1,
  warnings: [],
};

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

describe("useHealthQuery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and returns the health payload", async () => {
    vi.spyOn(healthService, "getHealth").mockResolvedValueOnce(health);

    const { result } = renderHook(() => useHealthQuery(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isPending).toBe(true);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(health);
  });
});
