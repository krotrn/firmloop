"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  settingsService,
  type SettingsUpdate,
} from "@/services/settings.service";

export const settingsQueryKeys = {
  all: ["settings"] as const,
};

export function useSettings() {
  return useQuery({
    queryKey: settingsQueryKeys.all,
    queryFn: () => settingsService.getSettings(),
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => settingsService.updateSettings(body),
    onSuccess: (settings) => {
      queryClient.setQueryData(settingsQueryKeys.all, settings);
      void queryClient.invalidateQueries({ queryKey: ["device"] });
      void queryClient.invalidateQueries({ queryKey: ["health"] });
    },
  });
}
