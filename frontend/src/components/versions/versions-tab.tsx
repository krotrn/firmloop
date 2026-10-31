"use client";

import * as React from "react";

import { RollbackDialog } from "@/components/versions/rollback-dialog";
import { VersionDiffDialog } from "@/components/versions/version-diff-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDevice } from "@/hooks/queries/use-device";
import { useRollback } from "@/hooks/queries/use-rollback";
import { useSessionVersions } from "@/hooks/queries/use-session-versions";
import { useSession } from "@/hooks/queries/use-sessions";
import { relativeTime } from "@/lib/time";
import type { Version } from "@/services/versions.service";

const NO_FIRMWARE_REASON = "Verified virtually only — no firmware to flash";

function VersionRow({
  version,
  isCurrent,
  isOnBoard,
  rollbackBlocked,
  rollbackDisabled,
  error,
  onDiff,
  onRollback,
}: {
  version: Version;
  isCurrent: boolean;
  isOnBoard: boolean;
  rollbackBlocked: boolean;
  rollbackDisabled: boolean;
  error: string | undefined;
  onDiff: () => void;
  onRollback: () => void;
}) {
  const canRollback = !(isCurrent && isOnBoard);
  const reasonId = `rollback-reason-${version.id}`;
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm font-semibold tabular-nums">
          v{version.number}
        </span>
        <Badge variant="outline">
          {version.target_kind === "real" ? "Real" : "Virtual"}
        </Badge>
        {isCurrent && <Badge variant="secondary">Current</Badge>}
        {isOnBoard && <Badge variant="success">On board</Badge>}
      </div>
      <p className="line-clamp-2 text-sm break-words">{version.spec.summary}</p>
      <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-xs">
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="tabular-nums">{version.tests_total} tests</span>
          {version.promoted_at && (
            <time
              dateTime={version.promoted_at}
              title={new Date(version.promoted_at).toLocaleString()}
            >
              {relativeTime(version.promoted_at)}
            </time>
          )}
        </span>
        <span className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onDiff}>
            Diff
          </Button>
          {canRollback && (
            <Button
              variant="outline"
              size="sm"
              disabled={rollbackDisabled}
              aria-describedby={rollbackBlocked ? reasonId : undefined}
              onClick={onRollback}
            >
              Roll back
            </Button>
          )}
        </span>
      </div>
      {canRollback && rollbackBlocked && (
        <p id={reasonId} className="text-muted-foreground text-xs">
          {NO_FIRMWARE_REASON}
        </p>
      )}
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
    </li>
  );
}

export function VersionsTab({ sessionId }: { sessionId: string }) {
  const versions = useSessionVersions(sessionId);
  const session = useSession(sessionId);
  const device = useDevice();
  const rollback = useRollback(sessionId);
  const [diffId, setDiffId] = React.useState<string | null>(null);
  const [rollbackId, setRollbackId] = React.useState<string | null>(null);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const items = React.useMemo(
    () => versions.data?.pages.flatMap((page) => page.items) ?? [],
    [versions.data]
  );
  const currentVersion = session.data?.current_version ?? null;
  const runningId = device.data?.running_version?.id ?? null;
  const realMode = device.data?.mode === "real";

  const confirmRollback = async () => {
    if (rollbackId === null) return;
    const id = rollbackId;
    setErrors((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    try {
      await rollback.mutateAsync(id);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not roll back";
      setErrors((current) => ({ ...current, [id]: message }));
    } finally {
      setRollbackId(null);
    }
  };

  let body;
  if (versions.isPending) {
    body = (
      <div role="status" aria-label="Loading versions" className="space-y-px">
        {[0, 1, 2].map((i) => (
          <div key={i} className="space-y-2 px-4 py-3">
            <Skeleton className="h-4 w-1/4" />
            <Skeleton className="h-3 w-3/4" />
          </div>
        ))}
      </div>
    );
  } else if (versions.isError && !versions.data) {
    body = (
      <div className="flex flex-col items-center gap-3 px-4 py-8 text-center">
        <p role="alert" className="text-muted-foreground text-sm">
          {versions.error.message}
        </p>
        <Button variant="outline" onClick={() => void versions.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else if (items.length === 0) {
    body = (
      <p className="text-muted-foreground px-4 py-8 text-center text-sm">
        No verified versions yet. A version appears here after a run passes its
        tests.
      </p>
    );
  } else {
    body = (
      <>
        <ul className="divide-border divide-y">
          {items.map((version) => (
            <VersionRow
              key={version.id}
              version={version}
              isCurrent={currentVersion?.id === version.id}
              isOnBoard={runningId === version.id}
              rollbackBlocked={realMode && version.firmware === null}
              rollbackDisabled={
                device.data === undefined ||
                (realMode && version.firmware === null)
              }
              error={errors[version.id]}
              onDiff={() => setDiffId(version.id)}
              onRollback={() => setRollbackId(version.id)}
            />
          ))}
        </ul>
        {versions.hasNextPage && (
          <div className="border-border flex flex-col items-center gap-2 border-t p-3">
            {versions.isFetchNextPageError && (
              <p role="alert" className="text-destructive text-sm">
                {versions.error?.message}
              </p>
            )}
            <Button
              variant="ghost"
              onClick={() => void versions.fetchNextPage()}
              disabled={versions.isFetchingNextPage}
            >
              {versions.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </>
    );
  }

  const diffVersion = items.find((v) => v.id === diffId);
  const rollbackVersion = items.find((v) => v.id === rollbackId);

  return (
    <section aria-labelledby="versions-heading">
      <h2
        id="versions-heading"
        className="text-muted-foreground mb-3 text-sm font-medium"
      >
        Versions
      </h2>
      <div className="border-border bg-card overflow-hidden rounded-xl border">
        {body}
      </div>
      {diffVersion && (
        <VersionDiffDialog
          version={diffVersion}
          versions={items}
          currentVersion={currentVersion}
          onClose={() => setDiffId(null)}
        />
      )}
      {rollbackVersion && (
        <RollbackDialog
          versionNumber={rollbackVersion.number}
          pending={rollback.isPending}
          onConfirm={() => void confirmRollback()}
          onClose={() => setRollbackId(null)}
        />
      )}
    </section>
  );
}
