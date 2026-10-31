import { CheckIcon, StarIcon, XIcon } from "lucide-react";

import type { Run } from "@/services/runs.service";

type Attempt = Run["attempts"][number];

function stageLabel(attempt: Attempt): string {
  switch (attempt.stage_reached) {
    case "generated":
      return "Code written";
    case "safety":
      return attempt.safety_ok === false ? "Safety failed" : "Safety checked";
    case "compile":
      return "Compile failed";
    case "tests":
      return "Tests run";
    default:
      return attempt.stage_reached;
  }
}

function Check({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === null) {
    return (
      <span className="text-muted-foreground">
        <span aria-hidden="true">-</span>
        <span className="sr-only">{label} not run</span>
      </span>
    );
  }
  const Icon = ok ? CheckIcon : XIcon;
  return (
    <span className="inline-flex items-center gap-1">
      <Icon aria-hidden="true" className="size-3.5" />
      {ok ? "Passed" : "Failed"}
    </span>
  );
}

function duration(ms: number | null): string {
  if (ms === null) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function tests(attempt: Attempt): string {
  if (attempt.tests_total === null) return "-";
  return `${attempt.tests_passed ?? 0}/${attempt.tests_total}`;
}

export function AttemptsList({ run }: { run: Run }) {
  return (
    <section
      aria-labelledby="run-attempts-heading"
      className="flex flex-col gap-3"
    >
      <h3 id="run-attempts-heading" className="text-sm font-medium">
        Attempts
      </h3>
      {run.attempts.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No attempts yet. They appear once code is written.
        </p>
      ) : (
        <div
          role="region"
          aria-label="Attempts table"
          tabIndex={0}
          className="focus-visible:ring-ring/50 overflow-x-auto rounded-md focus-visible:ring-3 focus-visible:outline-none"
        >
          <table className="w-full text-left text-sm tabular-nums">
            <thead className="text-muted-foreground text-xs">
              <tr>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Attempt
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Source
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Stage
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Safety
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Compile
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Tests
                </th>
                <th scope="col" className="py-1 font-medium">
                  Duration
                </th>
              </tr>
            </thead>
            <tbody>
              {run.attempts.map((attempt) => (
                <tr key={attempt.id} className="border-t align-top">
                  <th scope="row" className="py-2 pr-3 font-medium">
                    <span className="inline-flex items-center gap-1 whitespace-nowrap">
                      #{attempt.number}
                      {attempt.id === run.best_attempt_id && (
                        <span className="text-muted-foreground inline-flex items-center gap-1 text-xs font-normal">
                          <StarIcon aria-hidden="true" className="size-3" />
                          Best
                        </span>
                      )}
                    </span>
                  </th>
                  <td className="py-2 pr-3">{attempt.source}</td>
                  <td className="py-2 pr-3">{stageLabel(attempt)}</td>
                  <td className="py-2 pr-3">
                    <Check ok={attempt.safety_ok} label="Safety" />
                  </td>
                  <td className="py-2 pr-3">
                    <Check ok={attempt.compile_ok} label="Compile" />
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {tests(attempt)}
                  </td>
                  <td className="py-2 whitespace-nowrap">
                    {duration(attempt.duration_ms)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
