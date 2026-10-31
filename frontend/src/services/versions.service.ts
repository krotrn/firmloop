import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Version = components["schemas"]["VersionOut"];

export function getVersion(id: string): Promise<Version> {
  return fetcher<Version>(
    `${env.NEXT_PUBLIC_API_URL}/api/versions/${encodeURIComponent(id)}`
  );
}
