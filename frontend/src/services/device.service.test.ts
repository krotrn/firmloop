import { afterEach, describe, expect, it, vi } from "vitest";

import { deviceService } from "@/services/device.service";

describe("deviceService.getDevice", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the device status", async () => {
    const status = { mode: "virtual", connected: true, busy: false };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(status), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(deviceService.getDevice()).resolves.toEqual(status);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/device"
    );
  });
});
