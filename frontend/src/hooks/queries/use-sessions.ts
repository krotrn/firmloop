"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { sessionsService } from "@/services/sessions.service";

export const sessionsQueryKeys = {
  all: ["sessions"] as const,
};

export function useSessions() {
  return useInfiniteQuery({
    queryKey: sessionsQueryKeys.all,
    queryFn: ({ pageParam }) =>
      sessionsService.listSessions({ cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    // The list has no live events, so it is always refreshed on mount and focus.
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => sessionsService.createSession(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sessionsQueryKeys.all });
    },
  });
}
