"use client";

import * as React from "react";

import { sessionOf, useEvents } from "@/hooks/use-events";

const MAX_LINES = 500;
let nextLineId = 0;
const NO_LINES: RunLogLine[] = [];

export interface RunLogLine {
  level: "info" | "warn" | "error";
  message: string;
  ts: string;
  /** Increases with every received line; stable across the 500-line trimming. */
  id: number;
}

/**
 * Log lines of one run from `run.log` events, kept in memory (last 500). Lines of every run of
 * the session are collected while mounted, so switching to another run and back keeps them.
 */
export function useRunLog(
  sessionId: string,
  runId: string | null
): RunLogLine[] {
  const [store, setStore] = React.useState<{
    sessionId: string;
    lines: Record<string, RunLogLine[]>;
  }>({ sessionId, lines: {} });
  if (store.sessionId !== sessionId) {
    // Adjusting state during render: a new session starts with no lines.
    setStore({ sessionId, lines: {} });
  }

  useEvents((event) => {
    if (event.type !== "run.log") return;
    if (sessionOf(event) !== sessionId) return;
    const line: RunLogLine = {
      level: event.level,
      message: event.message,
      ts: event.ts,
      id: nextLineId++,
    };
    setStore((prev) => {
      if (prev.sessionId !== sessionId) return prev;
      const previous = prev.lines[event.run_id] ?? NO_LINES;
      return {
        sessionId,
        lines: {
          ...prev.lines,
          [event.run_id]: [...previous, line].slice(-MAX_LINES),
        },
      };
    });
  });

  if (runId === null || store.sessionId !== sessionId) return NO_LINES;
  return store.lines[runId] ?? NO_LINES;
}
