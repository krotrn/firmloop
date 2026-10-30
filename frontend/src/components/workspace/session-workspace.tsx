"use client";

import Link from "next/link";
import * as React from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RequestBox } from "@/components/workspace/request-box";
import { RunHistory } from "@/components/workspace/run-history";
import { RunPanel } from "@/components/workspace/run-panel";
import { SessionHeader } from "@/components/workspace/session-header";
import { SpecCard } from "@/components/spec/spec-card";
import { useDevice } from "@/hooks/queries/use-device";
import { useSessionRuns } from "@/hooks/queries/use-runs";
import { useSession } from "@/hooks/queries/use-sessions";
import { useSessionLive } from "@/hooks/use-session-live";
import { isApiError } from "@/lib/fetcher";

function WorkspaceSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading session"
      className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-8 sm:py-14"
    >
      <div className="mb-8 space-y-3">
        <Skeleton className="h-8 w-64 max-w-full" />
        <Skeleton className="h-5 w-48" />
      </div>
      <div className="grid gap-8 lg:grid-cols-[2fr_3fr]">
        <div className="space-y-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
        <Skeleton className="h-72 w-full" />
      </div>
    </div>
  );
}

export function SessionWorkspace({ id }: { id: string }) {
  const session = useSession(id);
  const runs = useSessionRuns(id);
  const device = useDevice();
  useSessionLive(id);
  // null follows the newest run; an id pins that run.
  const [pinnedRunId, setPinnedRunId] = React.useState<string | null>(null);

  if (session.isPending) return <WorkspaceSkeleton />;

  if (session.isError) {
    const notFound = isApiError(session.error) && session.error.status === 404;
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-4 px-4 py-20 text-center">
        {notFound ? (
          <>
            <h1 className="text-2xl font-semibold tracking-tight">
              Session not found
            </h1>
            <Link href="/" className={buttonVariants({ variant: "outline" })}>
              Back to sessions
            </Link>
          </>
        ) : (
          <>
            <p role="alert" className="text-muted-foreground text-sm">
              {session.error.message}
            </p>
            <Button variant="outline" onClick={() => void session.refetch()}>
              Retry
            </Button>
          </>
        )}
      </div>
    );
  }

  const items = runs.data?.pages.flatMap((page) => page.items) ?? [];
  const newest = items[0] ?? null;
  const pinned = pinnedRunId
    ? (items.find((run) => run.id === pinnedRunId) ?? null)
    : null;
  if (pinnedRunId && !pinned && runs.data) {
    // The pinned run is gone from the list: follow the newest again.
    setPinnedRunId(null);
  }
  const selected = pinned ?? newest;
  const latestStatus = newest?.status ?? session.data.last_run?.status ?? null;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-8 sm:py-14">
      <SessionHeader
        session={session.data}
        device={device.data}
        deviceIsPending={device.isPending}
        deviceIsError={device.isError}
      />
      <div className="flex flex-col gap-8 lg:grid lg:grid-cols-[2fr_3fr]">
        <div className="flex min-w-0 flex-col gap-8">
          <RequestBox
            sessionId={id}
            latestRunStatus={latestStatus}
            onSent={() => setPinnedRunId(null)}
          />
          {selected ? <SpecCard key={selected.id} run={selected} /> : null}
          <RunHistory
            runs={runs}
            selectedId={selected?.id ?? null}
            onSelect={(runId) =>
              setPinnedRunId(runId === newest?.id ? null : runId)
            }
          />
        </div>
        <RunPanel
          run={selected}
          sessionId={id}
          deviceMode={device.data?.mode ?? null}
        />
      </div>
    </div>
  );
}
