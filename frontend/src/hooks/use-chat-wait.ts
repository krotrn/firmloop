"use client";

import * as React from "react";

import { sessionOf, useEvents } from "@/hooks/use-events";

export type ChatWaitState = "idle" | "waiting" | "answered";

interface RunWait {
  pending: readonly string[];
  answered: boolean;
}

/**
 * Whether one run is waiting for the chat app to answer an LLM step, from `run.llm_waiting`
 * and `run.llm_answered`. The run ending clears any open wait.
 */
export function useChatWait(
  sessionId: string,
  runId: string | null
): ChatWaitState {
  const [store, setStore] = React.useState<{
    sessionId: string;
    runs: Record<string, RunWait>;
  }>({ sessionId, runs: {} });
  if (store.sessionId !== sessionId) {
    // Adjusting state during render: a new session starts with nothing waiting.
    setStore({ sessionId, runs: {} });
  }

  useEvents((event) => {
    if (
      event.type !== "run.llm_waiting" &&
      event.type !== "run.llm_answered" &&
      event.type !== "run.finished"
    ) {
      return;
    }
    if (sessionOf(event) !== sessionId) return;
    const id = event.run_id;
    setStore((prev) => {
      if (prev.sessionId !== sessionId) return prev;
      const current = prev.runs[id] ?? { pending: [], answered: false };
      let next: RunWait;
      if (event.type === "run.llm_waiting") {
        next = {
          pending: [
            ...current.pending.filter((t) => t !== event.task_id),
            event.task_id,
          ],
          answered: current.answered,
        };
      } else if (event.type === "run.llm_answered") {
        next = {
          pending: current.pending.filter((t) => t !== event.task_id),
          answered: true,
        };
      } else {
        next = { pending: [], answered: current.answered };
      }
      return { sessionId, runs: { ...prev.runs, [id]: next } };
    });
  });

  if (runId === null || store.sessionId !== sessionId) return "idle";
  const wait = store.runs[runId];
  if (!wait) return "idle";
  if (wait.pending.length > 0) return "waiting";
  return wait.answered ? "answered" : "idle";
}
