"use client";

import { CheckIcon, ChevronRightIcon, MinusIcon, XIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunAttempts } from "@/hooks/queries/use-attempts";
import { useRunTests } from "@/hooks/queries/use-run-tests";
import { describeExpectation } from "@/lib/expectation-words";
import type { Attempt } from "@/services/attempts.service";
import type { TestCase, TestResult } from "@/services/run-tests.service";
import type { Run } from "@/services/runs.service";

const ORIGIN_LABEL: Record<TestCase["origin"], string> = {
  scenario: "Scenario",
  boundary: "Boundary",
  truth_table: "Truth table",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function defaultAttempt(attempts: Attempt[], bestId: string | null): Attempt {
  return (
    attempts.find((a) => a.id === bestId) ??
    attempts.reduce((latest, a) => (a.number > latest.number ? a : latest))
  );
}

function ResultLabel({ result }: { result: TestResult | undefined }) {
  if (!result) {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1">
        <MinusIcon className="size-3.5" aria-hidden="true" />
        Not run
      </span>
    );
  }
  return result.passed ? (
    <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
      <CheckIcon className="size-3.5" aria-hidden="true" />
      Passed
    </span>
  ) : (
    <span className="text-destructive inline-flex items-center gap-1">
      <XIcon className="size-3.5" aria-hidden="true" />
      Failed
    </span>
  );
}

function FailureDetail({ result }: { result: TestResult }) {
  const failures = result.failures ?? [];
  return (
    <div className="flex flex-col gap-3">
      {result.error ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Error: </span>
          <code className="bg-muted rounded px-1 py-0.5 text-xs">
            {result.error}
          </code>
        </p>
      ) : null}
      {failures.map((f, i) => (
        <div key={i} className="flex flex-col gap-1.5">
          <p className="text-sm">
            <span className="font-medium">Step {f.step_index + 1}</span>
            <span className="text-muted-foreground"> · </span>
            <span>{f.message}</span>
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <span className="text-muted-foreground text-xs">Expected</span>
              <code className="bg-muted rounded p-2 font-mono text-xs break-words whitespace-pre-wrap">
                {describeExpectation(f.expectation)}
              </code>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-muted-foreground text-xs">Observed</span>
              <code className="bg-muted rounded p-2 font-mono text-xs break-words whitespace-pre-wrap">
                {f.observed_summary}
              </code>
            </div>
          </div>
        </div>
      ))}
      {!result.error && failures.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No failure details were recorded.
        </p>
      ) : null}
    </div>
  );
}

function TestRow({
  testCase,
  result,
}: {
  testCase: TestCase;
  result: TestResult | undefined;
}) {
  const detailId = React.useId();
  const [open, setOpen] = React.useState(false);
  const failed = result !== undefined && !result.passed;
  return (
    <>
      <tr className="border-b align-top">
        <td className="px-2 py-2 font-mono text-xs whitespace-nowrap">
          {failed ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={detailId}
              onClick={() => setOpen((v) => !v)}
              className="focus-visible:ring-ring/50 inline-flex items-center gap-1 rounded focus-visible:ring-[3px] focus-visible:outline-none"
            >
              <ChevronRightIcon
                className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`}
                aria-hidden="true"
              />
              {testCase.id}
            </button>
          ) : (
            testCase.id
          )}
        </td>
        <td className="px-2 py-2 whitespace-nowrap">
          <span className="inline-flex items-center gap-1.5">
            {ORIGIN_LABEL[testCase.origin]}
            {testCase.from_base ? (
              <Badge variant="outline">From base</Badge>
            ) : null}
          </span>
        </td>
        <td className="min-w-40 px-2 py-2">{testCase.scenario.title}</td>
        <td className="px-2 py-2 whitespace-nowrap">
          <ResultLabel result={result} />
        </td>
      </tr>
      {failed && open ? (
        <tr id={detailId} className="border-b">
          <td colSpan={4} className="bg-muted/30 px-3 py-3">
            <FailureDetail result={result} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function TestsTab({ run, sessionId }: { run: Run; sessionId: string }) {
  const testsQuery = useRunTests(sessionId, run.id);
  const attemptsQuery = useRunAttempts(sessionId, run.id);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  const failure = testsQuery.error ?? attemptsQuery.error;
  if (failure) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {errorMessage(failure)}
      </p>
    );
  }
  const cases = testsQuery.data;
  const attempts = attemptsQuery.data;
  if (!cases || !attempts) return <Skeleton className="h-48 w-full" />;
  if (cases.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No tests yet. The tests appear here once they are generated.
      </p>
    );
  }

  const attempt =
    attempts.length === 0
      ? null
      : (attempts.find((a) => a.id === selectedId) ??
        defaultAttempt(attempts, run.best_attempt_id));
  const results = new Map(
    (attempt?.test_results ?? []).map((r) => [r.test_id, r])
  );
  const passed = cases.filter((c) => results.get(c.id)?.passed).length;
  const attemptItems = attempts.map((a) => ({
    value: a.id,
    label:
      a.id === run.best_attempt_id
        ? `Attempt ${a.number} (best)`
        : `Attempt ${a.number}`,
  }));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        {attempt ? (
          <Select
            items={attemptItems}
            value={attempt.id}
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
        ) : null}
        <p className="text-sm font-medium">
          {attempt
            ? `${passed} of ${cases.length} passed`
            : `${cases.length} tests, none run yet`}
        </p>
      </div>
      <div
        role="region"
        aria-label="Tests"
        tabIndex={0}
        className="focus-visible:ring-ring/50 overflow-x-auto rounded-lg border focus-visible:ring-[3px] focus-visible:outline-none"
      >
        <table className="w-full text-left text-sm">
          <thead className="text-muted-foreground border-b text-xs">
            <tr>
              <th scope="col" className="px-2 py-2 font-medium">
                ID
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                Origin
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                Title
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                Result
              </th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <TestRow key={c.id} testCase={c} result={results.get(c.id)} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
