"use client";

import type {
  InfiniteData,
  UseInfiniteQueryResult,
} from "@tanstack/react-query";

import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import type { Run, RunList } from "@/services/runs.service";

export function runLabel(run: Run): string {
  if (run.kind === "rollback") {
    const number = run.base_version?.number ?? run.result_version?.number;
    return number != null ? `Rollback to v${number}` : "Rollback";
  }
  if (run.kind === "manual") return "Manual edit";
  return run.request_text ?? "Request";
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: Run;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const number = run.result_version?.number;
  return (
    <li>
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(run.id)}
        className={cn(
          "hover:bg-muted/60 focus-visible:ring-ring/50 flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-inset",
          selected && "bg-muted"
        )}
      >
        <span className="line-clamp-2 text-sm font-medium break-words">
          {runLabel(run)}
        </span>
        <span className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <RunStatusBadge status={run.status} />
          {number != null && <span className="tabular-nums">v{number}</span>}
          <time
            dateTime={run.started_at}
            title={new Date(run.started_at).toLocaleString()}
          >
            {relativeTime(run.started_at)}
          </time>
        </span>
      </button>
    </li>
  );
}

export function RunHistory({
  runs,
  selectedId,
  onSelect,
}: {
  runs: UseInfiniteQueryResult<InfiniteData<RunList>, Error>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const items = runs.data?.pages.flatMap((page) => page.items) ?? [];

  let body;
  if (runs.isPending) {
    body = (
      <div role="status" aria-label="Loading runs" className="space-y-px">
        {[0, 1, 2].map((i) => (
          <div key={i} className="space-y-2 px-4 py-3">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        ))}
      </div>
    );
  } else if (runs.isError && !runs.data) {
    body = (
      <div className="flex flex-col items-center gap-3 px-4 py-8 text-center">
        <p role="alert" className="text-muted-foreground text-sm">
          {runs.error.message}
        </p>
        <Button variant="outline" onClick={() => void runs.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else if (items.length === 0) {
    body = (
      <p className="text-muted-foreground px-4 py-8 text-center text-sm">
        No runs yet. Describe what the board should do.
      </p>
    );
  } else {
    body = (
      <>
        <ul className="divide-border divide-y">
          {items.map((run) => (
            <RunRow
              key={run.id}
              run={run}
              selected={run.id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
        {runs.hasNextPage && (
          <div className="border-border flex flex-col items-center gap-2 border-t p-3">
            {runs.isFetchNextPageError && (
              <p role="alert" className="text-destructive text-sm">
                {runs.error?.message}
              </p>
            )}
            <Button
              variant="ghost"
              onClick={() => void runs.fetchNextPage()}
              disabled={runs.isFetchingNextPage}
            >
              {runs.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="run-history-heading">
      <h2
        id="run-history-heading"
        className="text-muted-foreground mb-3 text-sm font-medium"
      >
        Runs
      </h2>
      <div className="border-border bg-card overflow-hidden rounded-xl border">
        {body}
      </div>
    </section>
  );
}
