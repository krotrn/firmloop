"use client";

import { useQueryClient } from "@tanstack/react-query";

import { sessionsQueryKeys } from "@/hooks/queries/use-sessions";
import { sessionOf, useEvents, useRunEvents } from "@/hooks/use-events";

const REFETCH_EVENTS = new Set([
  "run.created",
  "run.status",
  "run.question",
  "run.spec_proposed",
  "run.finished",
]);

/**
 * Subscribes to one session and refetches its detail and runs when a run is created,
 * changes status, asks a question, proposes a spec or finishes. Returns the run events.
 */
export function useSessionLive(sessionId: string) {
  const queryClient = useQueryClient();
  const live = useRunEvents(sessionId);

  useEvents((event) => {
    if (!REFETCH_EVENTS.has(event.type)) return;
    if (sessionOf(event) !== sessionId) return;
    void queryClient.invalidateQueries({
      queryKey: sessionsQueryKeys.detail(sessionId),
    });
  });

  return live;
}
