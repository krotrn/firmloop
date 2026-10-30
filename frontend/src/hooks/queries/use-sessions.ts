"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { isApiError } from "@/lib/fetcher";
import {
  sessionsService,
  type PatchSessionBody,
  type Session,
} from "@/services/sessions.service";

export const sessionsQueryKeys = {
  all: ["sessions"] as const,
  detail: (id: string) => ["sessions", id] as const,
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

export function useSession(id: string) {
  return useQuery({
    queryKey: sessionsQueryKeys.detail(id),
    queryFn: () => sessionsService.getSession(id),
    // A missing or invalid session will not appear by asking again.
    retry: (failureCount, error) =>
      !(isApiError(error) && error.status >= 400 && error.status < 500) &&
      failureCount < 2,
  });
}

export function useUpdateSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PatchSessionBody) =>
      sessionsService.updateSession(id, body),
    onSuccess: (session: Session) => {
      queryClient.setQueryData(sessionsQueryKeys.detail(id), session);
      void queryClient.invalidateQueries({ queryKey: sessionsQueryKeys.all });
    },
  });
}

export function useArchiveSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => sessionsService.archiveSession(id),
    onSuccess: () => {
      // Only the list: refetching the archived session's own detail could flash
      // "not found" before the page navigates away.
      void queryClient.invalidateQueries({
        queryKey: sessionsQueryKeys.all,
        exact: true,
      });
    },
  });
}
