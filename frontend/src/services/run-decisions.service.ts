import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { Run } from "@/services/runs.service";
import type { components } from "@/types/generated/api";

export type Spec = components["schemas"]["Spec"];

const runUrl = (runId: string, action: string) =>
  `${env.NEXT_PUBLIC_API_URL}/api/runs/${encodeURIComponent(runId)}/${action}`;

export function answerRun(runId: string, answer: string): Promise<Run> {
  const body: components["schemas"]["AnswerBody"] = { answer };
  return fetcher<Run>(runUrl(runId, "answer"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function confirmRun(runId: string, spec?: Spec): Promise<Run> {
  const body: components["schemas"]["ConfirmBody"] =
    spec === undefined ? {} : { spec };
  return fetcher<Run>(runUrl(runId, "confirm"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function rejectRun(runId: string, feedback: string): Promise<Run> {
  const body: components["schemas"]["RejectBody"] = { feedback };
  return fetcher<Run>(runUrl(runId, "reject"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}
