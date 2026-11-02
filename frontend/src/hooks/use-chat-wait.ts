"use client";

import * as React from "react";

import { sessionOf, useEvents } from "@/hooks/use-events";

export type ChatWaitState = "idle" | "waiting" | "answered";

interface RunWait {
  pending: readonly string[];
  /** Task ids the events reported as answered; a seeded task in here is no longer waiting. */
  closed: readonly string[];
  answered: boolean;
  finished: boolean;
}

const EMPTY_WAIT: RunWait = {
  pending: [],
  closed: [],
  answered: false,
  finished: false,
};

/**
 * Whether one run is waiting for the chat app to answer an LLM step. `seededTaskId` is the task
 * the loaded run reports as pending, so the wait shows after a reload; `run.llm_waiting` and
 * `run.llm_answered` take over from there, and the run ending clears any open wait. A seed that
 * the run no longer reports (null) is gone.
 */
export function useChatWait(
  sessionId: string,
  runId: string | null,
  seededTaskId: string | null = null
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
      const current = prev.runs[id] ?? EMPTY_WAIT;
      let next: RunWait;
      if (event.type === "run.llm_waiting") {
        next = {
          pending: [
            ...current.pending.filter((t) => t !== event.task_id),
            event.task_id,
          ],
          closed: current.closed.filter((t) => t !== event.task_id),
          answered: current.answered,
          finished: false,
        };
      } else if (event.type === "run.llm_answered") {
        next = {
          pending: current.pending.filter((t) => t !== event.task_id),
          closed: [...current.closed, event.task_id],
          answered: true,
          finished: current.finished,
        };
      } else {
        next = { ...current, pending: [], finished: true };
      }
      return { sessionId, runs: { ...prev.runs, [id]: next } };
    });
  });

  if (runId === null || store.sessionId !== sessionId) return "idle";
  const wait = store.runs[runId] ?? EMPTY_WAIT;
  const seeded =
    seededTaskId !== null &&
    !wait.finished &&
    !wait.closed.includes(seededTaskId);
  if (wait.pending.length > 0 || seeded) return "waiting";
  return wait.answered ? "answered" : "idle";
}
