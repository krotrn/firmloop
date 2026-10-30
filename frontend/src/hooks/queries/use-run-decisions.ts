"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  answerRun,
  confirmRun,
  rejectRun,
  type Spec,
} from "@/services/run-decisions.service";
import { sessionsQueryKeys } from "@/hooks/queries/use-sessions";
import type { Run } from "@/services/runs.service";

function useInvalidateSession(run: Run) {
  const queryClient = useQueryClient();
  return () =>
    void queryClient.invalidateQueries({
      queryKey: sessionsQueryKeys.detail(run.session_id),
    });
}

export function useAnswerRun(run: Run) {
  const invalidate = useInvalidateSession(run);
  return useMutation({
    mutationFn: (answer: string) => answerRun(run.id, answer),
    onSuccess: invalidate,
  });
}

export function useConfirmRun(run: Run) {
  const invalidate = useInvalidateSession(run);
  return useMutation({
    mutationFn: (spec: Spec | undefined) => confirmRun(run.id, spec),
    onSuccess: invalidate,
  });
}

export function useRejectRun(run: Run) {
  const invalidate = useInvalidateSession(run);
  return useMutation({
    mutationFn: (feedback: string) => rejectRun(run.id, feedback),
    onSuccess: invalidate,
  });
}
