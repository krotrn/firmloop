"use client";

import { useInfiniteQuery } from "@tanstack/react-query";

import { listSessionVersions } from "@/services/session-versions.service";

export const sessionVersionsQueryKey = (sessionId: string) =>
  ["sessions", sessionId, "versions"] as const;

export function useSessionVersions(sessionId: string) {
  return useInfiniteQuery({
    queryKey: sessionVersionsQueryKey(sessionId),
    queryFn: ({ pageParam }) =>
      listSessionVersions(sessionId, { status: "promoted", cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}
