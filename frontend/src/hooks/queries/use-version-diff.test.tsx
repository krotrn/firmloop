import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useVersionDiff } from "./use-version-diff";
import * as service from "@/services/session-versions.service";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useVersionDiff", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the diff against another version", async () => {
    const spy = vi.spyOn(service, "diffVersions").mockResolvedValue({
      code_old: "a",
      code_new: "b",
    } as service.VersionDiff);
    const { result } = renderHook(() => useVersionDiff("v2", "v1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("v2", "v1");
    expect(result.current.data?.code_new).toBe("b");
  });

  it("does not fetch without an against version", () => {
    const spy = vi.spyOn(service, "diffVersions");
    renderHook(() => useVersionDiff("v2", null), { wrapper });
    expect(spy).not.toHaveBeenCalled();
  });
});
