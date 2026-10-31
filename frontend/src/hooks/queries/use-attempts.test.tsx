import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { attemptsQueryKey, useRunAttempts } from "./use-attempts";
import * as service from "@/services/attempts.service";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useRunAttempts", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("builds a key under the session so session invalidation refreshes it", () => {
    expect(attemptsQueryKey("s1", "r1")).toEqual([
      "sessions",
      "s1",
      "runs",
      "r1",
      "attempts",
    ]);
  });

  it("loads the attempts of the run", async () => {
    const spy = vi
      .spyOn(service, "listRunAttempts")
      .mockResolvedValue([{ id: "a1" } as service.Attempt]);
    const { result } = renderHook(() => useRunAttempts("s1", "r1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("r1");
    expect(result.current.data).toEqual([{ id: "a1" }]);
  });
});
