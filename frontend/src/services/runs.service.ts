import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Run = components["schemas"]["RunOut"];

export type RunList = components["schemas"]["RunList"];

export type CreateRunBody = components["schemas"]["CreateRunBody"];

export interface ListSessionRunsParams {
  cursor?: string | null;
  limit?: number;
}

export const runsService = {
  listSessionRuns: (
    sessionId: string,
    { cursor, limit }: ListSessionRunsParams = {}
  ): Promise<RunList> => {
    const query: string[] = [];
    if (cursor) query.push(`cursor=${encodeURIComponent(cursor)}`);
    if (limit !== undefined) query.push(`limit=${limit}`);
    const suffix = query.length > 0 ? `?${query.join("&")}` : "";
    return fetcher<RunList>(
      `${env.NEXT_PUBLIC_API_URL}/api/sessions/${encodeURIComponent(sessionId)}/runs${suffix}`
    );
  },

  createRun: (sessionId: string, body: CreateRunBody): Promise<Run> =>
    fetcher<Run>(
      `${env.NEXT_PUBLIC_API_URL}/api/sessions/${encodeURIComponent(sessionId)}/runs`,
      { method: "POST", body: JSON.stringify(body) }
    ),

  cancelRun: (runId: string): Promise<Run> =>
    fetcher<Run>(
      `${env.NEXT_PUBLIC_API_URL}/api/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" }
    ),
};
