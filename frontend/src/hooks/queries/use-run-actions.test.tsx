import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useManualRun, useRetryRun } from "./use-run-actions";
import * as service from "@/services/run-actions.service";
import type { Run } from "@/services/runs.service";

const runWithCancelWaiting = vi.fn();
vi.mock("@/providers/cancel-waiting-provider", () => ({
  useCancelWaiting: () => ({ runWithCancelWaiting }),
}));

const created = { id: "new" } as Run;

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

describe("run action hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    runWithCancelWaiting.mockReset();
    runWithCancelWaiting.mockImplementation(
      (send: (cancelWaiting: boolean) => Promise<unknown>) => send(true)
    );
  });

  it("posts manual code through the cancel-waiting flow and invalidates the session", async () => {
    const spy = vi.spyOn(service, "manualRun").mockResolvedValue(created);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useManualRun("s1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ runId: "r1", code: "code" });
    });
    expect(spy).toHaveBeenCalledWith("r1", "code", true);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("posts the retry spec through the cancel-waiting flow", async () => {
    const spy = vi.spyOn(service, "retryRun").mockResolvedValue(created);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useRetryRun("s1"), { wrapper });
    const spec = { summary: "s", rules: [], scenarios: [] };
    await act(async () => {
      await result.current.mutateAsync({ runId: "r1", spec });
    });
    expect(spy).toHaveBeenCalledWith("r1", spec, true);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("does not invalidate when the user keeps the waiting run", async () => {
    runWithCancelWaiting.mockResolvedValue(undefined);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useManualRun("s1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ runId: "r1", code: "code" });
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
