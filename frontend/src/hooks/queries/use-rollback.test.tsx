import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRollback } from "./use-rollback";
import type { Run } from "@/services/runs.service";
import * as service from "@/services/session-versions.service";

const runWithCancelWaiting = vi.fn();
vi.mock("@/providers/cancel-waiting-provider", () => ({
  useCancelWaiting: () => ({ runWithCancelWaiting }),
}));

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

describe("useRollback", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    runWithCancelWaiting.mockReset();
    runWithCancelWaiting.mockImplementation(
      (send: (cancelWaiting: boolean) => Promise<unknown>) => send(true)
    );
  });

  it("rolls back through the cancel-waiting flow and invalidates the session", async () => {
    const spy = vi
      .spyOn(service, "rollbackVersion")
      .mockResolvedValue({ id: "run" } as Run);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useRollback("s1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("v1");
    });
    expect(spy).toHaveBeenCalledWith("v1", true);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] });
  });

  it("does not invalidate when the user keeps the waiting run", async () => {
    runWithCancelWaiting.mockResolvedValue(undefined);
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useRollback("s1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("v1");
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
