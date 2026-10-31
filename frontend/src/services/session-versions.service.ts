import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { Run } from "@/services/runs.service";
import type { components } from "@/types/generated/api";

export type VersionList = components["schemas"]["VersionList"];
export type VersionDiff = components["schemas"]["VersionDiffOut"];
export type SpecDiff = components["schemas"]["SpecDiff"];

export interface ListSessionVersionsParams {
  status: "promoted";
  cursor?: string | null;
}

const base = env.NEXT_PUBLIC_API_URL;

export function listSessionVersions(
  sessionId: string,
  { status, cursor }: ListSessionVersionsParams
): Promise<VersionList> {
  const query = [`status=${status}`];
  if (cursor) query.push(`cursor=${encodeURIComponent(cursor)}`);
  return fetcher<VersionList>(
    `${base}/api/sessions/${encodeURIComponent(sessionId)}/versions?${query.join("&")}`
  );
}

/** Changes from `against` (old) to `id` (new). */
export function diffVersions(
  id: string,
  against: string
): Promise<VersionDiff> {
  return fetcher<VersionDiff>(
    `${base}/api/versions/${encodeURIComponent(id)}/diff?against=${encodeURIComponent(against)}`
  );
}

export function rollbackVersion(
  id: string,
  cancelWaiting = false
): Promise<Run> {
  const body: components["schemas"]["RollbackIn"] = {
    cancel_waiting: cancelWaiting,
  };
  return fetcher<Run>(
    `${base}/api/versions/${encodeURIComponent(id)}/rollback`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
}
