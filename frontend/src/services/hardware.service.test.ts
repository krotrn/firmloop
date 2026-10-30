import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/fetcher";
import { hardwareService, type Hardware } from "@/services/hardware.service";

const hardware: Hardware = {
  components: [
    {
      id: "dht22",
      name: "DHT22",
      kind: "sensor",
      pins: { data: 4 },
      channels: ["temp"],
      ai_notes: "temp in °C",
    },
  ],
  channels: [
    {
      id: "temp",
      type: "float",
      kind: "sensor",
      unit: "°C",
      range: [-40, 80],
      resolution: 0.1,
      neutral: 22,
      default_hysteresis: 0.5,
      injectable: true,
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("hardwareService.getHardware", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the hardware registry and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(hardware));
    vi.stubGlobal("fetch", fetchMock);

    const result = await hardwareService.getHardware();

    expect(result).toEqual(hardware);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/hardware"
    );
  });

  it("throws ApiError on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error: { code: "INTERNAL", message: "boom", details: {} } },
            500
          )
        )
    );
    const err = await hardwareService.getHardware().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
  });
});
