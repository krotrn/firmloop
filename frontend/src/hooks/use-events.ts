"use client";

import * as React from "react";

import {
  useEventsConnection,
  type ConnectionStatus,
} from "@/providers/events-provider";
import type { ServerEvent } from "@/types/generated/events";

const MAX_RUN_EVENTS = 1000;
const RUN_EVENT_PREFIXES = ["run.", "attempt.", "deploy.", "verify."];

function sessionOf(event: ServerEvent): string | null {
  if (event.type === "run.created") {
    const id: unknown = event.run.session_id;
    return typeof id === "string" ? id : null;
  }
  return "session_id" in event ? event.session_id : null;
}

/** Calls `handler` with every validated event; the latest handler is always used. */
export function useEvents(handler: (event: ServerEvent) => void) {
  const { addListener } = useEventsConnection();
  const handlerRef = React.useRef(handler);

  React.useEffect(() => {
    handlerRef.current = handler;
  });

  React.useEffect(
    () => addListener((event) => handlerRef.current(event)),
    [addListener]
  );
}

/**
 * Subscribes to one session and returns its run, attempt, deploy and verify events
 * received since mount (oldest first, last 1000). Pass null to stay unsubscribed.
 */
export function useRunEvents(sessionId: string | null): {
  events: ServerEvent[];
  status: ConnectionStatus;
} {
  const { status, addListener, subscribeSession } = useEventsConnection();
  // The events are tagged with their session so a stale list is never shown for a new id.
  const [stored, setStored] = React.useState<{
    sessionId: string | null;
    events: ServerEvent[];
  }>({ sessionId, events: [] });
  if (stored.sessionId !== sessionId) {
    // Adjusting state during render: a new id starts with no events.
    setStored({ sessionId, events: [] });
  }

  React.useEffect(() => {
    if (sessionId === null) return;
    const unsubscribe = subscribeSession(sessionId);
    const removeListener = addListener((event) => {
      if (!RUN_EVENT_PREFIXES.some((prefix) => event.type.startsWith(prefix)))
        return;
      if (sessionOf(event) !== sessionId) return;
      setStored((prev) => {
        const previous = prev.sessionId === sessionId ? prev.events : [];
        return {
          sessionId,
          events: [...previous, event].slice(-MAX_RUN_EVENTS),
        };
      });
    });
    return () => {
      removeListener();
      unsubscribe();
    };
  }, [sessionId, addListener, subscribeSession]);

  return {
    events: stored.sessionId === sessionId ? stored.events : [],
    status,
  };
}
