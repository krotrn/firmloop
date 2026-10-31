import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { Run } from "@/services/runs.service";
import type { components } from "@/types/generated/api";

export type Spec = components["schemas"]["Spec"];

const runUrl = (runId: string, action: string) =>
  `${env.NEXT_PUBLIC_API_URL}/api/runs/${encodeURIComponent(runId)}/${action}`;

export function manualRun(
  runId: string,
  code: string,
  cancelWaiting = false
): Promise<Run> {
  const body: components["schemas"]["ManualBody"] = {
    code,
    cancel_waiting: cancelWaiting,
  };
  return fetcher<Run>(runUrl(runId, "manual"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function retryRun(
  runId: string,
  spec: Spec,
  cancelWaiting = false
): Promise<Run> {
  const body: components["schemas"]["RetryBody"] = {
    spec,
    cancel_waiting: cancelWaiting,
  };
  return fetcher<Run>(runUrl(runId, "retry"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}
