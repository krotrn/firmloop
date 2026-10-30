import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Session = components["schemas"]["SessionOut"];

export const sessionsService = {
  getSession: (id: string): Promise<Session> =>
    fetcher<Session>(
      `${env.NEXT_PUBLIC_API_URL}/api/sessions/${encodeURIComponent(id)}`
    ),
};
