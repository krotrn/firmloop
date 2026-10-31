"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { sessionsQueryKeys } from "@/hooks/queries/use-sessions";
import { useCancelWaiting } from "@/providers/cancel-waiting-provider";
import { rollbackVersion } from "@/services/session-versions.service";

/** Resolves to undefined when the user chose to keep the waiting run. */
export function useRollback(sessionId: string) {
  const { runWithCancelWaiting } = useCancelWaiting();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) =>
      runWithCancelWaiting((cancelWaiting) =>
        rollbackVersion(versionId, cancelWaiting)
      ),
    onSuccess: (run) => {
      if (run === undefined) return;
      void queryClient.invalidateQueries({
        queryKey: sessionsQueryKeys.detail(sessionId),
      });
    },
  });
}
