"use client";

import { useState } from "react";

import { ScenarioEditor } from "@/components/spec/scenario-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  useAnswerRun,
  useConfirmRun,
  useRejectRun,
} from "@/hooks/queries/use-run-decisions";
import { useHardware } from "@/hooks/queries/use-hardware";
import { describeExpectation } from "@/lib/expectation-words";
import { isApiError } from "@/lib/fetcher";
import {
  msToSeconds,
  validateScenarios,
  wasEdited,
  type Scenario,
} from "@/lib/spec-edit";
import type { Run } from "@/services/runs.service";
import type { components } from "@/types/generated/api";

type Spec = components["schemas"]["Spec"];
type IdDiff = components["schemas"]["IdDiff"];

const MAX_TEXT = 2000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Turn a path such as "scenarios[0].steps[1].expect[0].channel" into readable words. */
export function describePath(path: string, spec: Spec): string {
  const parts: string[] = [];
  let scenario: Scenario | undefined;
  for (const m of path.matchAll(/\.?(\w+)\[(\d+)\]|\.(\w+)/g)) {
    const [, name, index, plain] = m;
    if (name !== undefined && index !== undefined) {
      const n = Number(index);
      if (name === "scenarios") {
        scenario = spec.scenarios[n];
        parts.push(`Scenario ${scenario?.id ?? n + 1}`);
      } else if (name === "rules") {
        parts.push(`Rule ${spec.rules[n]?.id ?? n + 1}`);
      } else if (name === "steps") {
        parts.push(`step ${n + 1}`);
      } else if (name === "expect") {
        parts.push(`expectation ${n + 1}`);
      } else {
        parts.push(`${name} ${n + 1}`);
      }
    } else if (plain !== undefined) {
      parts.push(plain.replace(/_/g, " "));
    }
  }
  return parts.join(" › ");
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

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function diffBadge(id: string, diff: IdDiff | undefined) {
  if (diff?.added?.includes(id)) return <Badge>New</Badge>;
  if (diff?.modified?.includes(id))
    return <Badge variant="secondary">Changed</Badge>;
  return null;
}

function removedLine(run: Run): string | null {
  const ids = [
    ...(run.spec_diff?.scenarios.removed ?? []),
    ...(run.spec_diff?.rules.removed ?? []),
  ];
  return ids.length > 0 ? `Removed: ${ids.join(", ")}` : null;
}

function ErrorText({ error, spec }: { error: unknown; spec: Spec }) {
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

function ScenarioSummary({ scenario }: { scenario: Scenario }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-sm font-medium">{scenario.title}</span>
      <ul className="text-muted-foreground text-sm tabular-nums">
        {scenario.steps.map((step, i) => {
          const sets = Object.entries(step.set ?? {})
            .map(([k, v]) => `${k} = ${String(v)}`)
            .join(", ");
          const expects = step.expect.map(describeExpectation).join("; ");
          return (
            <li key={i}>
              At {msToSeconds(step.at_ms)} s{sets ? ` set ${sets}` : ""}
              {expects ? ` expect ${expects}` : ""}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function QuestionCard({ run }: { run: Run }) {
  const [answer, setAnswer] = useState("");
  const mutation = useAnswerRun(run);
  const trimmed = answer.trim();
  const valid = trimmed.length >= 1 && trimmed.length <= MAX_TEXT;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{run.question?.question}</CardTitle>
        {run.question?.reason ? (
          <p className="text-muted-foreground text-sm">{run.question.reason}</p>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Textarea
          aria-label="Your answer"
          value={answer}
          maxLength={MAX_TEXT}
          onChange={(e) => setAnswer(e.target.value)}
        />
        <ErrorText
          error={mutation.error}
          spec={{ summary: "", rules: [], scenarios: [] }}
        />
      </CardContent>
      <CardFooter>
        <Button
          disabled={!valid || mutation.isPending}
          onClick={() =>
            mutation.mutate(trimmed, { onSuccess: () => setAnswer("") })
          }
        >
          Send answer
        </Button>
      </CardFooter>
    </Card>
  );
}

function SpecView({
  run,
  spec,
  readOnly,
}: {
  run: Run;
  spec: components["schemas"]["Spec"];
  readOnly: boolean;
}) {
  const hardwareQuery = useHardware();
  const hardware = hardwareQuery.data;
  const channels = hardware?.channels ?? [];
  const [editing, setEditing] = useState(false);
  const [scenarios, setScenarios] = useState<Scenario[]>(spec.scenarios);
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const confirm = useConfirmRun(run);
  const reject = useRejectRun(run);
  const diff = readOnly ? null : run.spec_diff;
  const edited = wasEdited(spec.scenarios, scenarios);
  const issues = edited ? validateScenarios(scenarios, channels) : [];
  const feedbackTrimmed = feedback.trim();
  const removed = readOnly ? null : removedLine(run);
  const shown = edited ? scenarios : spec.scenarios;
  const unchanged = (id: string) => {
    const original = spec.scenarios.find((o) => o.id === id);
    const current = scenarios.find((o) => o.id === id);
    return (
      original !== undefined && !wasEdited([original], [current ?? original])
    );
  };
  const editScenarios = (next: Scenario[]) => {
    confirm.reset();
    setScenarios(next);
  };
  const confirmSpec: Spec = { ...spec, scenarios };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {readOnly ? "Confirmed spec" : "Proposed spec"}
          {diff?.summary_changed ? (
            <Badge variant="secondary">Changed</Badge>
          ) : null}
        </CardTitle>
        <p className="text-sm">{spec.summary}</p>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {run.boundaries.length > 0 ? (
          <div className="flex flex-col gap-1">
            <h3 className="text-muted-foreground text-xs font-medium">
              Boundaries
            </h3>
            <ul className="list-disc pl-5 text-sm">
              {run.boundaries.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {spec.rules.length > 0 ? (
          <div className="flex flex-col gap-2">
            <h3 className="text-muted-foreground text-xs font-medium">Rules</h3>
            {spec.rules.map((r) => (
              <div
                key={r.id}
                className="flex flex-wrap items-center gap-2 text-sm"
              >
                <span>{r.description}</span>
                {diffBadge(r.id, diff?.rules)}
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex flex-col gap-3">
          <h3 className="text-muted-foreground text-xs font-medium">
            Scenarios
          </h3>
          {editing ? (
            <ScenarioEditor
              value={scenarios}
              onChange={editScenarios}
              channels={channels}
            />
          ) : (
            shown.map((s) => (
              <div key={s.id} className="flex flex-col gap-1">
                <div className="flex flex-wrap gap-1.5 empty:hidden">
                  {diffBadge(s.id, diff?.scenarios)}
                  {unchanged(s.id) ? null : (
                    <Badge variant="secondary">Edited</Badge>
                  )}
                </div>
                <ScenarioSummary scenario={s} />
              </div>
            ))
          )}
        </div>
        {removed ? (
          <p className="text-muted-foreground text-sm">{removed}</p>
        ) : null}
        {rejecting ? (
          <div className="flex flex-col gap-2">
            <Textarea
              aria-label="What should change?"
              value={feedback}
              maxLength={MAX_TEXT}
              onChange={(e) => {
                reject.reset();
                setFeedback(e.target.value);
              }}
            />
            <div className="flex gap-2">
              <Button
                variant="destructive"
                disabled={
                  feedbackTrimmed.length < 1 ||
                  feedbackTrimmed.length > MAX_TEXT ||
                  reject.isPending
                }
                onClick={() => reject.mutate(feedbackTrimmed)}
              >
                Send feedback
              </Button>
              <Button variant="ghost" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
        {issues.length > 0 ? (
          <p role="alert" className="text-destructive text-sm">
            {issues.length === 1
              ? "1 problem to fix before you can confirm."
              : `${issues.length} problems to fix before you can confirm.`}
            {editing ? "" : " Choose Edit scenarios to see them."}
          </p>
        ) : null}
        <ErrorText error={confirm.error} spec={confirmSpec} />
        <ErrorText error={reject.error} spec={confirmSpec} />
      </CardContent>
      {readOnly ? null : (
        <CardFooter className="flex flex-wrap gap-2">
          <Button
            disabled={issues.length > 0 || confirm.isPending}
            onClick={() =>
              confirm.mutate(edited ? { ...spec, scenarios } : undefined)
            }
          >
            Confirm
          </Button>
          <Button
            variant="outline"
            disabled={!hardware}
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Done editing" : "Edit scenarios"}
          </Button>
          <Button variant="ghost" onClick={() => setRejecting(true)}>
            Reject…
          </Button>
          {hardware ? null : (
            <p className="text-muted-foreground w-full text-sm">
              {hardwareQuery.isError
                ? "The hardware list could not be loaded, so scenarios cannot be edited."
                : "Loading the hardware list…"}
            </p>
          )}
        </CardFooter>
      )}
    </Card>
  );
}

export function SpecCard({ run }: { run: Run }) {
  if (run.status === "awaiting_answer" && run.question) {
    return <QuestionCard run={run} />;
  }
  if (run.status === "awaiting_confirmation" && run.proposed_spec) {
    return (
      <SpecView
        key={`${run.id}:${run.status}`}
        run={run}
        spec={run.proposed_spec}
        readOnly={false}
      />
    );
  }
  if (run.confirmed_spec) {
    return (
      <SpecView
        key={`${run.id}:${run.status}`}
        run={run}
        spec={run.confirmed_spec}
        readOnly
      />
    );
  }
  return null;
}
