"use client";

import { useQuery } from "@tanstack/react-query";

import { listRunTests } from "@/services/run-tests.service";

export const runTestsQueryKey = (sessionId: string, runId: string) =>
  ["sessions", sessionId, "runs", runId, "tests"] as const;

export function useRunTests(sessionId: string, runId: string | null) {
  return useQuery({
    queryKey: runTestsQueryKey(sessionId, runId ?? ""),
    queryFn: () => listRunTests(runId as string),
    enabled: runId !== null,
  });
}
