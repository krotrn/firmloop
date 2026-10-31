import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  sessionVersionsQueryKey,
  useSessionVersions,
} from "./use-session-versions";
import * as service from "@/services/session-versions.service";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useSessionVersions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keys the list under the session so session invalidation refreshes it", () => {
    expect(sessionVersionsQueryKey("s1")).toEqual([
      "sessions",
      "s1",
      "versions",
    ]);
  });

  it("loads promoted versions and follows next_cursor", async () => {
    const spy = vi
      .spyOn(service, "listSessionVersions")
      .mockResolvedValueOnce({
        items: [{ id: "v2" } as service.VersionList["items"][number]],
        next_cursor: "c1",
      })
      .mockResolvedValueOnce({
        items: [{ id: "v1" } as service.VersionList["items"][number]],
        next_cursor: null,
      });
    const { result } = renderHook(() => useSessionVersions("s1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("s1", {
      status: "promoted",
      cursor: null,
    });
    expect(result.current.hasNextPage).toBe(true);
    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(spy).toHaveBeenLastCalledWith("s1", {
      status: "promoted",
      cursor: "c1",
    });
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
  });
});
