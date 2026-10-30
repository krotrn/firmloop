import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useAnswerRun,
  useConfirmRun,
  useRejectRun,
} from "@/hooks/queries/use-run-decisions";
import * as service from "@/services/run-decisions.service";
import type { Run } from "@/services/runs.service";

const run = { id: "r1", session_id: "s1" } as Run;

const setup = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { invalidate, wrapper };
};

describe("run decision hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("answers and invalidates the session", async () => {
    const spy = vi.spyOn(service, "answerRun").mockResolvedValue(run);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useAnswerRun(run), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("yes");
    });
    expect(spy).toHaveBeenCalledWith("r1", "yes");
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] })
    );
  });

  it("confirms with and without a spec", async () => {
    const spy = vi.spyOn(service, "confirmRun").mockResolvedValue(run);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useConfirmRun(run), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(undefined);
    });
    expect(spy).toHaveBeenCalledWith("r1", undefined);
    const spec = { summary: "s", rules: [], scenarios: [] };
    await act(async () => {
      await result.current.mutateAsync(spec);
    });
    expect(spy).toHaveBeenLastCalledWith("r1", spec);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("rejects with feedback and invalidates the session", async () => {
    const spy = vi.spyOn(service, "rejectRun").mockResolvedValue(run);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useRejectRun(run), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("nope");
    });
    expect(spy).toHaveBeenCalledWith("r1", "nope");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });
});
