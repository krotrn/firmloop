import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Settings = components["schemas"]["SettingsOut"];

export type SettingsUpdate = components["schemas"]["SettingsUpdate"];

export const settingsService = {
  getSettings: (): Promise<Settings> =>
    fetcher<Settings>(`${env.NEXT_PUBLIC_API_URL}/api/settings`),

  updateSettings: (body: SettingsUpdate): Promise<Settings> =>
    fetcher<Settings>(`${env.NEXT_PUBLIC_API_URL}/api/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
