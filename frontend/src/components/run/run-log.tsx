"use client";

import * as React from "react";

import type { RunLogLine } from "@/hooks/use-run-log";
import { cn } from "@/lib/utils";

const PREFIX: Record<RunLogLine["level"], string> = {
  info: "",
  warn: "warn: ",
  error: "error: ",
};

const STICK_THRESHOLD_PX = 24;

export function RunLog({ lines }: { lines: RunLogLine[] }) {
  const box = React.useRef<HTMLDivElement>(null);
  const following = React.useRef(true);

  React.useEffect(() => {
    const el = box.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  function onScroll() {
    const el = box.current;
    if (!el) return;
    following.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD_PX;
  }

  return (
    <section aria-labelledby="run-log-heading" className="flex flex-col gap-3">
      <h3 id="run-log-heading" className="text-sm font-medium">
        Log
      </h3>
      <div
        ref={box}
        role="log"
        aria-label="Run log"
        aria-live="polite"
        tabIndex={0}
        onScroll={onScroll}
        className="bg-muted/40 focus-visible:ring-ring/50 max-h-64 min-h-24 overflow-auto rounded-md border p-3 font-mono text-xs focus-visible:ring-3 focus-visible:outline-none"
      >
        {lines.length === 0 ? (
          <p className="text-muted-foreground">
            Lines appear here while the run works.
          </p>
        ) : (
          lines.map((line) => (
            <div
              key={line.id}
              className={cn(
                "break-words whitespace-pre-wrap",
                line.level === "warn" && "text-amber-700 dark:text-amber-400",
                line.level === "error" && "text-destructive font-medium"
              )}
            >
              {PREFIX[line.level]}
              {line.message}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
