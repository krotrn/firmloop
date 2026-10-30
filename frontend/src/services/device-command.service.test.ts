import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/fetcher";
import { deviceCommandService } from "@/services/device-command.service";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("deviceCommandService", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts a set_sensor command", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deviceCommandService.setSensor("temp", 31.5)).resolves.toEqual(
      { ok: true }
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/device/command");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      action: "set_sensor",
      channel: "temp",
      value: 31.5,
    });
  });

  it("sends booleans as booleans", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await deviceCommandService.setSensor("motion", true);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string).value).toBe(true);
  });

  it("throws ApiError with the server's code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            error: {
              code: "RUN_ACTIVE",
              message: "A run is active",
              details: {},
            },
          },
          409
        )
      )
    );
    await expect(
      deviceCommandService.setSensor("temp", 1)
    ).rejects.toMatchObject({
      constructor: ApiError,
      status: 409,
      code: "RUN_ACTIVE",
    });
  });
});
