import { afterEach, describe, expect, it, vi } from "vitest";

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

const stub = (body: unknown) => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

describe("settingsService", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("gets /api/settings", async () => {
    const fetchMock = stub(settings);
    await expect(settingsService.getSettings()).resolves.toEqual(settings);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/settings$/);
    expect(init?.method).toBeUndefined();
  });

  it("puts only the given keys", async () => {
    const fetchMock = stub(settings);
    await settingsService.updateSettings({
      max_attempts: 5,
      llm_fallback_provider: null,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/settings$/);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({
      max_attempts: 5,
      llm_fallback_provider: null,
    });
  });
});
