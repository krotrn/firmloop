import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  settingsQueryKeys,
  useSettings,
  useUpdateSettings,
} from "@/hooks/queries/use-settings";
import { settingsService, type Settings } from "@/services/settings.service";

const settings: Settings = {
  auto_approve: false,
  available_providers: ["fake"],
  device_mode: "virtual",
  llm_fallback_provider: null,
  llm_model: "m",
  llm_provider: "fake",
  max_attempts: 3,
  speak_results: false,
  voice_input_enabled: true,
};

const setup = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
};

describe("settings hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads settings under the settings key", async () => {
    vi.spyOn(settingsService, "getSettings").mockResolvedValue(settings);
    const { queryClient, wrapper } = setup();
    const { result } = renderHook(() => useSettings(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryData(settingsQueryKeys.all)).toEqual(settings);
  });

  it("stores the response and invalidates device and health", async () => {
    const saved: Settings = { ...settings, max_attempts: 5 };
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue(saved);
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useUpdateSettings(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ max_attempts: 5 });
    });

    expect(update).toHaveBeenCalledWith({ max_attempts: 5 });
    expect(queryClient.getQueryData(settingsQueryKeys.all)).toEqual(saved);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["device"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["health"] });
  });
});
