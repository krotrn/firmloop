import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { runTestsQueryKey, useRunTests } from "./use-run-tests";
import * as service from "@/services/run-tests.service";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useRunTests", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("builds a key under the session so session invalidation refreshes it", () => {
    expect(runTestsQueryKey("s1", "r1")).toEqual([
      "sessions",
      "s1",
      "runs",
      "r1",
      "tests",
    ]);
  });

  it("loads the test cases of the run", async () => {
    const spy = vi
      .spyOn(service, "listRunTests")
      .mockResolvedValue([{ id: "t1" } as service.TestCase]);
    const { result } = renderHook(() => useRunTests("s1", "r1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("r1");
  });

  it("does not fetch without a run", () => {
    const spy = vi.spyOn(service, "listRunTests");
    const { result } = renderHook(() => useRunTests("s1", null), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });
});
