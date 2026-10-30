import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Health = components["schemas"]["HealthOut"];

export const healthService = {
  getHealth: (): Promise<Health> =>
    fetcher<Health>(`${env.NEXT_PUBLIC_API_URL}/api/health`),
};
