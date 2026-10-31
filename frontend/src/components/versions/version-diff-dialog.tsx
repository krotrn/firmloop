"use client";

import * as React from "react";

import { CodeDiff } from "@/components/code/code-diff";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useVersion } from "@/hooks/queries/use-version";
import { useVersionDiff } from "@/hooks/queries/use-version-diff";
import type { SpecDiff } from "@/services/session-versions.service";
import type { Version } from "@/services/versions.service";

interface VersionRef {
  id: string;
  number: number | null;
}

const numberLabel = (number: number | null) =>
  number != null ? `v${number}` : "Earlier version";

/**
 * The version to compare with by default: the session's current version, else the
 * parent, else the next older one in the list.
 */
export function defaultAgainst(
  version: Version,
  versions: Version[],
  current: VersionRef | null
): string | null {
  if (current && current.id !== version.id) return current.id;
  if (version.parent_version_id) return version.parent_version_id;
  const index = versions.findIndex((v) => v.id === version.id);
  const older = index >= 0 ? versions[index + 1] : undefined;
  return older?.id ?? null;
}

function compareOptions(
  version: Version,
  versions: Version[],
  current: VersionRef | null,
  extraRefs: VersionRef[]
): { value: string; label: string }[] {
  const refs = new Map<string, VersionRef>();
  for (const v of versions) refs.set(v.id, { id: v.id, number: v.number });
  if (current && !refs.has(current.id)) refs.set(current.id, current);
  for (const ref of extraRefs) {
    if (!refs.has(ref.id)) refs.set(ref.id, ref);
  }
  refs.delete(version.id);
  return [...refs.values()].map((r) => ({
    value: r.id,
    label: numberLabel(r.number),
  }));
}

const GROUPS = [
  ["added", "Added"],
  ["modified", "Changed"],
  ["removed", "Removed"],
] as const;

function IdChanges({
  title,
  diff,
}: {
  title: string;
  diff: SpecDiff["rules"];
}) {
  const present = GROUPS.filter(([key]) => (diff[key] ?? []).length > 0);
  if (present.length === 0) return null;
  return (
    <div role="group" aria-label={title} className="flex flex-col gap-1.5">
      <h4 className="text-muted-foreground text-xs font-medium">{title}</h4>
      {present.map(([key, label]) => (
        <div key={key} className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 text-xs">{label}</span>
          {(diff[key] ?? []).map((id) => (
            <Badge
              key={id}
              variant={
                key === "added"
                  ? "success"
                  : key === "removed"
                    ? "destructive"
                    : "secondary"
              }
              className="font-mono"
            >
              {id}
            </Badge>
          ))}
        </div>
      ))}
    </div>
  );
}

function SpecChanges({ diff }: { diff: SpecDiff }) {
  const unchanged =
    !diff.summary_changed &&
    GROUPS.every(
      ([key]) =>
        (diff.rules[key] ?? []).length === 0 &&
        (diff.scenarios[key] ?? []).length === 0
    );
  if (unchanged) {
    return <p className="text-muted-foreground text-sm">No spec changes.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {diff.summary_changed && (
        <div className="flex flex-col gap-1 text-sm">
          <h4 className="text-muted-foreground text-xs font-medium">Summary</h4>
          <p className="text-muted-foreground line-through decoration-1">
            {diff.summary_old}
          </p>
          <p>{diff.summary_new}</p>
        </div>
      )}
      <IdChanges title="Rules" diff={diff.rules} />
      <IdChanges title="Scenarios" diff={diff.scenarios} />
    </div>
  );
}

export function VersionDiffDialog({
  version,
  versions,
  currentVersion,
  onClose,
}: {
  version: Version;
  versions: Version[];
  currentVersion: VersionRef | null;
  onClose: () => void;
}) {
  const [chosen, setChosen] = React.useState<string | null>(null);
  const against = chosen ?? defaultAgainst(version, versions, currentVersion);
  // Versions outside the loaded pages are looked up so they carry their number.
  const known = (id: string | null) =>
    id === null ||
    versions.some((v) => v.id === id) ||
    currentVersion?.id === id;
  const parentId = version.parent_version_id;
  const parent = useVersion(known(parentId) ? null : parentId);
  const chosenLookup = useVersion(known(against) ? null : against);
  const extraRefs: VersionRef[] = [];
  for (const [id, lookup] of [
    [parentId, parent],
    [against, chosenLookup],
  ] as const) {
    if (id && !known(id)) {
      extraRefs.push({ id, number: lookup.data?.number ?? null });
    }
  }
  const options = compareOptions(version, versions, currentVersion, extraRefs);
  const diff = useVersionDiff(version.id, against);
  const thisLabel = numberLabel(version.number);
  const againstLabel =
    options.find((o) => o.value === against)?.label ?? numberLabel(null);

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Changes in {thisLabel}</DialogTitle>
          <DialogDescription>
            Changes from {againstLabel} to {thisLabel}.
          </DialogDescription>
        </DialogHeader>
        {against === null ? (
          <p className="text-muted-foreground text-sm">
            No other version to compare with.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>Compare with</span>
              <Select
                items={options}
                value={against}
                onValueChange={(value) => {
                  if (value) setChosen(value);
                }}
              >
                <SelectTrigger aria-label="Compare with" className="min-w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {options.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {diff.isPending ? (
              <div
                role="status"
                aria-label="Loading diff"
                className="space-y-2"
              >
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-48 w-full" />
              </div>
            ) : diff.isError ? (
              <p role="alert" className="text-destructive text-sm">
                {diff.error.message}
              </p>
            ) : (
              <div className="flex flex-col gap-4">
                <SpecChanges diff={diff.data.spec_diff} />
                <CodeDiff
                  original={diff.data.code_old}
                  modified={diff.data.code_new}
                  originalLabel={againstLabel}
                  modifiedLabel={thisLabel}
                />
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
