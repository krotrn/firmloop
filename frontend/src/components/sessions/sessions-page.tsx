"use client";

import { Loader2Icon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { EmptyState } from "@/components/feedback/empty-state";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateSession, useSessions } from "@/hooks/queries/use-sessions";
import { relativeTime } from "@/lib/time";
import type { Session } from "@/services/sessions.service";

function NewSessionButton({
  onClick,
  pending,
}: {
  onClick: () => void;
  pending: boolean;
}) {
  return (
    <Button onClick={onClick} disabled={pending}>
      {pending ? (
        <Loader2Icon className="animate-spin" data-icon="inline-start" />
      ) : (
        <PlusIcon data-icon="inline-start" />
      )}
      New session
    </Button>
  );
}

function SessionRow({ session }: { session: Session }) {
  const version = session.current_version;
  return (
    <li>
      <Link
        href={`/sessions/${encodeURIComponent(session.id)}`}
        className="hover:bg-muted/60 focus-visible:ring-ring/50 focus-visible:bg-muted/60 flex flex-wrap items-center justify-between gap-x-6 gap-y-1.5 px-4 py-3.5 transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-inset"
      >
        <span className="min-w-0 flex-1 basis-full truncate text-sm font-medium sm:basis-48">
          {session.title}
        </span>
        <span className="flex flex-1 basis-48 items-center gap-4 text-sm sm:flex-none">
          <span className="text-muted-foreground w-8 tabular-nums">
            {version?.number != null ? `v${version.number}` : "—"}
          </span>
          {session.last_run ? (
            <RunStatusBadge status={session.last_run.status} />
          ) : (
            <span className="text-muted-foreground text-xs">No runs</span>
          )}
          <time
            dateTime={session.updated_at}
            title={new Date(session.updated_at).toLocaleString()}
            className="text-muted-foreground ml-auto text-right text-xs sm:w-24"
          >
            {relativeTime(session.updated_at)}
          </time>
        </span>
      </Link>
    </li>
  );
}

function SkeletonRows() {
  return (
    <div
      className="divide-border divide-y"
      role="status"
      aria-label="Loading sessions"
      aria-busy="true"
    >
      {Array.from({ length: 4 }, (_, i) => (
        <div
          key={i}
          className="flex items-center justify-between gap-6 px-4 py-4"
        >
          <Skeleton className="h-4 w-48 max-w-[50%]" />
          <Skeleton className="h-4 w-32" />
        </div>
      ))}
    </div>
  );
}

export function SessionsPage() {
  const router = useRouter();
  const sessions = useSessions();
  const create = useCreateSession();

  const startSession = async () => {
    try {
      const created = await create.mutateAsync();
      router.push(`/sessions/${encodeURIComponent(created.id)}`);
    } catch {
      // The failure is shown inline from the mutation state.
    }
  };

  const items = sessions.data?.pages.flatMap((page) => page.items) ?? [];
  const newSession = (
    <NewSessionButton
      onClick={() => void startSession()}
      pending={create.isPending || create.isSuccess}
    />
  );

  let body;
  if (sessions.isPending) {
    body = <SkeletonRows />;
  } else if (sessions.isError && !sessions.data) {
    body = (
      <div className="flex flex-col items-center gap-4 px-4 py-12 text-center">
        <p role="alert" className="text-muted-foreground text-sm">
          {sessions.error.message}
        </p>
        <Button variant="outline" onClick={() => void sessions.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else if (items.length === 0) {
    body = null;
  } else {
    body = (
      <>
        <ul className="divide-border divide-y">
          {items.map((session) => (
            <SessionRow key={session.id} session={session} />
          ))}
        </ul>
        {sessions.hasNextPage && (
          <div className="border-border flex flex-col items-center gap-2 border-t p-3">
            {sessions.isFetchNextPageError && (
              <p role="alert" className="text-destructive text-sm">
                {sessions.error?.message}
              </p>
            )}
            <Button
              variant="ghost"
              onClick={() => void sessions.fetchNextPage()}
              disabled={sessions.isFetchingNextPage}
            >
              {sessions.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </>
    );
  }

  const isEmpty = sessions.isSuccess && items.length === 0;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-8 sm:py-14">
      <div className="mb-8 flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        {!isEmpty && newSession}
      </div>
      {create.isError && (
        <p role="alert" className="text-destructive mb-4 text-sm">
          {create.error.message}
        </p>
      )}
      {isEmpty ? (
        <EmptyState
          title="No sessions yet"
          description="Describe what you want your ESP32 to do and FirmLoop will build and test it."
          action={newSession}
        />
      ) : (
        <div className="border-border bg-card overflow-hidden rounded-xl border">
          {body}
        </div>
      )}
    </div>
  );
}
