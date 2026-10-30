import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/fetcher";
import { healthService, type Health } from "@/services/health.service";

const health: Health = {
  ok: true,
  services: { builder: "ok", device: "ok", llm: "ok", runner: "ok" },
  lib_version: "0.1.0",
  proto: 1,
  warnings: [],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("healthService.getHealth", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the backend health URL and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(health));
    vi.stubGlobal("fetch", fetchMock);

    const result = await healthService.getHealth();

    expect(result).toEqual(health);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/health"
    );
  });

  it("does not send a Content-Type header on a bodyless GET (avoids a CORS preflight)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(health));
    vi.stubGlobal("fetch", fetchMock);

    await healthService.getHealth();

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.has("Content-Type")).toBe(false);
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
    const err = await healthService.getHealth().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).code).toBe("INTERNAL");
  });
});
