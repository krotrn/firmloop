import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useVersion } from "./use-version";
import * as service from "@/services/versions.service";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useVersion", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the version", async () => {
    const spy = vi
      .spyOn(service, "getVersion")
      .mockResolvedValue({ id: "v1", code: "x" } as service.Version);
    const { result } = renderHook(() => useVersion("v1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("v1");
    expect(result.current.data?.code).toBe("x");
  });

  it("does not fetch without an id", () => {
    const spy = vi.spyOn(service, "getVersion");
    const { result } = renderHook(() => useVersion(null), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });
});
