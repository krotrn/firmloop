"use client";

import { Loader2Icon } from "lucide-react";
import * as React from "react";

import { CodeDiff } from "@/components/code/code-diff";
import { CodeView } from "@/components/code/code-view";
import { describePath } from "@/components/spec/spec-card";
import { ScenarioEditor } from "@/components/spec/scenario-editor";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useHardware } from "@/hooks/queries/use-hardware";
import { useRunAttempts } from "@/hooks/queries/use-attempts";
import { useManualRun, useRetryRun } from "@/hooks/queries/use-run-actions";
import { useVersion } from "@/hooks/queries/use-version";
import { isApiError } from "@/lib/fetcher";
import { validateScenarios, type Scenario } from "@/lib/spec-edit";
import type { Attempt } from "@/services/attempts.service";
import type { Spec } from "@/services/run-actions.service";
import type { Run } from "@/services/runs.service";

const EMPTY_SPEC: Spec = { summary: "", rules: [], scenarios: [] };

type Mode = "view" | "manual" | "scenarios";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function specErrors(error: unknown, spec: Spec): string[] | null {
  if (!isApiError(error, "SPEC_INVALID")) return null;
  const list: unknown = error.details.errors;
  if (!Array.isArray(list)) return [error.message];
  return (list as unknown[]).map((e) => {
    if (typeof e === "string") return e;
    if (!isRecord(e)) return "Invalid";
    const message = typeof e.message === "string" ? e.message : "Invalid";
    return typeof e.path === "string"
      ? `${describePath(e.path, spec)}: ${message}`
      : message;
  });
}

function ActionError({ error, spec }: { error: unknown; spec: Spec }) {
  if (!error) return null;
  const list = specErrors(error, spec);
  if (list) {
    return (
      <div role="alert" className="text-destructive text-sm">
        <p>The spec has problems:</p>
        <ul className="list-disc pl-5">
          {list.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <p role="alert" className="text-destructive text-sm">
      {errorMessage(error)}
    </p>
  );
}

function defaultAttempt(attempts: Attempt[], bestId: string | null): Attempt {
  return (
    attempts.find((a) => a.id === bestId) ??
    attempts.reduce((latest, a) => (a.number > latest.number ? a : latest))
  );
}

function Findings({ attempt }: { attempt: Attempt }) {
  return (
    <>
      {attempt.safety_violations.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Safety violations</h3>
          <ul className="flex flex-col gap-1.5 text-sm">
            {attempt.safety_violations.map((v, i) => (
              <li
                key={i}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5"
              >
                <span className="text-muted-foreground tabular-nums">
                  Line {v.line}
                </span>
                <code className="bg-muted rounded px-1 py-0.5 text-xs">
                  {v.rule}
                </code>
                <span>{v.message}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {attempt.compile_log ? (
        <details open={attempt.compile_ok === false} className="text-sm">
          <summary className="cursor-pointer font-medium">Compile log</summary>
          <pre className="bg-muted mt-2 max-h-72 overflow-auto rounded-lg p-3 font-mono text-xs whitespace-pre-wrap">
            {attempt.compile_log}
          </pre>
        </details>
      ) : null}
    </>
  );
}

/**
 * Mount with `key={run.id}` so the draft, the selected attempt and the open
 * editors start fresh for every run.
 */
export function CodeTab({ run, sessionId }: { run: Run; sessionId: string }) {
  const attemptsQuery = useRunAttempts(sessionId, run.id);
  const hardware = useHardware();
  const manual = useManualRun(sessionId);
  const retry = useRetryRun(sessionId);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [diffOn, setDiffOn] = React.useState(false);
  const [mode, setMode] = React.useState<Mode>("view");
  const [draft, setDraft] = React.useState("");
  const [scenarios, setScenarios] = React.useState<Scenario[]>([]);

  const baseVersion = run.base_version;
  const version = useVersion(diffOn && baseVersion ? baseVersion.id : null);

  const attempts = attemptsQuery.data;
  if (attemptsQuery.isError) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {errorMessage(attemptsQuery.error)}
      </p>
    );
  }
  if (!attempts) return <Skeleton className="h-64 w-full" />;
  if (attempts.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No code yet. The code appears here once it is written.
      </p>
    );
  }

  const attempt =
    attempts.find((a) => a.id === selectedId) ??
    defaultAttempt(attempts, run.best_attempt_id);
  const code = attempt.code ?? "";
  const confirmed = run.status === "failed" ? run.confirmed_spec : null;
  const canManual = confirmed !== null;
  const canRetry = confirmed !== null && run.kind === "request";
  const channels = hardware.data?.channels ?? [];
  const issues =
    mode === "scenarios" ? validateScenarios(scenarios, channels) : [];
  const pending = manual.isPending || retry.isPending;
  const attemptItems = attempts.map((a) => ({
    value: a.id,
    label:
      a.id === run.best_attempt_id
        ? `Attempt ${a.number} (best)`
        : `Attempt ${a.number}`,
  }));

  const leave = () => {
    setMode("view");
    manual.reset();
    retry.reset();
  };

  const submitManual = async () => {
    const created = await manual.mutateAsync({ runId: run.id, code: draft });
    if (created !== undefined) leave();
  };

  const submitRetry = async () => {
    if (!confirmed) return;
    const created = await retry.mutateAsync({
      runId: run.id,
      spec: { ...confirmed, scenarios },
    });
    if (created !== undefined) leave();
  };

  const showDiff = diffOn && mode === "view" && baseVersion;
  const baseLabel = baseVersion
    ? baseVersion.number === null
      ? "base"
      : `v${baseVersion.number}`
    : "";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={attemptItems}
          value={attempt.id}
          disabled={mode !== "view"}
          onValueChange={(value) => {
            if (value) setSelectedId(value);
          }}
        >
          <SelectTrigger aria-label="Attempt" className="min-w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {attemptItems.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {baseVersion && mode === "view" ? (
          <Button
            variant="outline"
            size="sm"
            aria-pressed={diffOn}
            onClick={() => setDiffOn((v) => !v)}
          >
            Diff vs {baseLabel}
          </Button>
        ) : null}
      </div>

      {showDiff ? (
        version.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {errorMessage(version.error)}
          </p>
        ) : version.data ? (
          <CodeDiff
            original={version.data.code}
            modified={code}
            originalLabel={baseLabel}
            modifiedLabel={`Attempt ${attempt.number}`}
          />
        ) : (
          <Skeleton className="h-64 w-full" />
        )
      ) : (
        <CodeView
          label="Attempt code"
          value={mode === "manual" ? draft : code}
          onChange={
            mode === "manual"
              ? (next) => {
                  manual.reset();
                  setDraft(next);
                }
              : undefined
          }
        />
      )}

      {mode === "scenarios" && confirmed ? (
        <div className="flex flex-col gap-3">
          {hardware.data ? (
            <ScenarioEditor
              value={scenarios}
              onChange={(next) => {
                retry.reset();
                setScenarios(next);
              }}
              channels={channels}
            />
          ) : (
            <p className="text-muted-foreground text-sm">
              {hardware.isError
                ? "The hardware list could not be loaded, so scenarios cannot be edited."
                : "Loading the hardware list…"}
            </p>
          )}
          {issues.length > 0 ? (
            <p role="alert" className="text-destructive text-sm">
              {issues.length === 1
                ? "1 problem to fix before you can run again."
                : `${issues.length} problems to fix before you can run again.`}
            </p>
          ) : null}
          <ActionError error={retry.error} spec={{ ...confirmed, scenarios }} />
        </div>
      ) : null}
      {mode === "manual" ? (
        <ActionError error={manual.error} spec={confirmed ?? EMPTY_SPEC} />
      ) : null}

      <Findings attempt={attempt} />

      {canManual || canRetry ? (
        <div className="flex flex-wrap gap-2">
          {mode === "view" ? (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  setDraft(code);
                  setMode("manual");
                }}
              >
                Edit &amp; run manually
              </Button>
              {canRetry ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setScenarios(confirmed.scenarios);
                    setMode("scenarios");
                  }}
                >
                  Edit scenarios &amp; retry
                </Button>
              ) : null}
            </>
          ) : (
            <>
              <Button
                disabled={
                  pending ||
                  (mode === "scenarios" &&
                    (issues.length > 0 || !hardware.data))
                }
                onClick={() =>
                  void (
                    mode === "manual" ? submitManual() : submitRetry()
                  ).catch(() => undefined)
                }
              >
                {pending ? (
                  <Loader2Icon
                    className="animate-spin"
                    data-icon="inline-start"
                  />
                ) : null}
                {mode === "manual" ? "Run this code" : "Run again"}
              </Button>
              <Button variant="ghost" disabled={pending} onClick={leave}>
                Cancel
              </Button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
