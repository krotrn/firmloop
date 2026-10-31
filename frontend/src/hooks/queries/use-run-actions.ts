"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { sessionsQueryKeys } from "@/hooks/queries/use-sessions";
import { useCancelWaiting } from "@/providers/cancel-waiting-provider";
import { manualRun, retryRun, type Spec } from "@/services/run-actions.service";

function useInvalidateSession(sessionId: string) {
  const queryClient = useQueryClient();
  return () =>
    void queryClient.invalidateQueries({
      queryKey: sessionsQueryKeys.detail(sessionId),
    });
}

/** Resolves to undefined when the user chose to keep the waiting run. */
export function useManualRun(sessionId: string) {
  const { runWithCancelWaiting } = useCancelWaiting();
  const invalidate = useInvalidateSession(sessionId);
  return useMutation({
    mutationFn: ({ runId, code }: { runId: string; code: string }) =>
      runWithCancelWaiting((cancelWaiting) =>
        manualRun(runId, code, cancelWaiting)
      ),
    onSuccess: (run) => {
      if (run !== undefined) invalidate();
    },
  });
}

/** Resolves to undefined when the user chose to keep the waiting run. */
export function useRetryRun(sessionId: string) {
  const { runWithCancelWaiting } = useCancelWaiting();
  const invalidate = useInvalidateSession(sessionId);
  return useMutation({
    mutationFn: ({ runId, spec }: { runId: string; spec: Spec }) =>
      runWithCancelWaiting((cancelWaiting) =>
        retryRun(runId, spec, cancelWaiting)
      ),
    onSuccess: (run) => {
      if (run !== undefined) invalidate();
    },
  });
}
