"use client";

import { useQuery } from "@tanstack/react-query";

import { listRunAttempts } from "@/services/attempts.service";

export const attemptsQueryKey = (sessionId: string, runId: string) =>
  ["sessions", sessionId, "runs", runId, "attempts"] as const;

export function useRunAttempts(sessionId: string, runId: string) {
  return useQuery({
    queryKey: attemptsQueryKey(sessionId, runId),
    queryFn: () => listRunAttempts(runId),
  });
}
