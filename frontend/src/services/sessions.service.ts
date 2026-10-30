import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Session = components["schemas"]["SessionOut"];

export type SessionList = components["schemas"]["SessionList"];

export interface ListSessionsParams {
  cursor?: string | null;
  limit?: number;
}

export const sessionsService = {
  listSessions: ({
    cursor,
    limit,
  }: ListSessionsParams = {}): Promise<SessionList> => {
    const query = ["archived=false"];
    if (cursor) query.push(`cursor=${encodeURIComponent(cursor)}`);
    if (limit !== undefined) query.push(`limit=${limit}`);
    return fetcher<SessionList>(
      `${env.NEXT_PUBLIC_API_URL}/api/sessions?${query.join("&")}`
    );
  },

  createSession: (): Promise<Session> =>
    fetcher<Session>(`${env.NEXT_PUBLIC_API_URL}/api/sessions`, {
      method: "POST",
      body: "{}",
    }),

  getSession: (id: string): Promise<Session> =>
    fetcher<Session>(
      `${env.NEXT_PUBLIC_API_URL}/api/sessions/${encodeURIComponent(id)}`
    ),
};
