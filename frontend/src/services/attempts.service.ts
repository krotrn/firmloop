import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Attempt = components["schemas"]["AttemptOut"];

export async function listRunAttempts(runId: string): Promise<Attempt[]> {
  const list = await fetcher<components["schemas"]["AttemptList"]>(
    `${env.NEXT_PUBLIC_API_URL}/api/runs/${encodeURIComponent(runId)}/attempts`
  );
  return list.items;
}
