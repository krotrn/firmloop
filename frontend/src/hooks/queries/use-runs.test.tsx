import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  runsQueryKeys,
  useCancelRun,
  useCreateRun,
  useSessionRuns,
} from "@/hooks/queries/use-runs";
import { runsService, type Run } from "@/services/runs.service";

const run = (id: string) => ({ id }) as Run;

const setup = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
};

describe("runs hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("exposes the per-session key under the session detail key", () => {
    expect(runsQueryKeys.bySession("s1")).toEqual(["sessions", "s1", "runs"]);
  });

  it("pages runs with next_cursor", async () => {
    const list = vi
      .spyOn(runsService, "listSessionRuns")
      .mockResolvedValueOnce({ items: [run("a")], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [run("b")], next_cursor: null });
    const { wrapper } = setup();
    const { result } = renderHook(() => useSessionRuns("s1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(list).toHaveBeenLastCalledWith("s1", { cursor: null });
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(list).toHaveBeenLastCalledWith("s1", { cursor: "c1" });
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
  });

  it("creates a text run and invalidates the session", async () => {
    const create = vi
      .spyOn(runsService, "createRun")
      .mockResolvedValue(run("r1"));
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateRun("s1"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        requestText: "blink",
        cancelWaiting: true,
      });
    });
    expect(create).toHaveBeenCalledWith("s1", {
      request_text: "blink",
      input_mode: "text",
      cancel_waiting: true,
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("cancels a run and invalidates the session", async () => {
    const cancel = vi
      .spyOn(runsService, "cancelRun")
      .mockResolvedValue(run("r1"));
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCancelRun("s1"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync("r1");
    });
    expect(cancel).toHaveBeenCalledWith("r1");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });
});
