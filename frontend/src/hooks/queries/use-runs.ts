"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { sessionsQueryKeys } from "@/hooks/queries/use-sessions";
import { runsService } from "@/services/runs.service";

export const runsQueryKeys = {
  bySession: (sessionId: string) => ["sessions", sessionId, "runs"] as const,
};

export function useSessionRuns(sessionId: string) {
  return useInfiniteQuery({
    queryKey: runsQueryKeys.bySession(sessionId),
    queryFn: ({ pageParam }) =>
      runsService.listSessionRuns(sessionId, { cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

export function useCreateRun(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      requestText,
      cancelWaiting,
    }: {
      requestText: string;
      cancelWaiting: boolean;
    }) =>
      runsService.createRun(sessionId, {
        request_text: requestText,
        input_mode: "text",
        cancel_waiting: cancelWaiting,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: sessionsQueryKeys.detail(sessionId),
      });
    },
  });
}

export function useCancelRun(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => runsService.cancelRun(runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: sessionsQueryKeys.detail(sessionId),
      });
    },
  });
}
