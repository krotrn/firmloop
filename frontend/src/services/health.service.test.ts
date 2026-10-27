import { afterEach, describe, expect, it, vi } from "vitest";

import { FetchError } from "@/lib/fetcher";
import { healthService } from "@/services/health.service";

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
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ok: true, version: "0.1.0" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await healthService.getHealth();

    expect(result).toEqual({ ok: true, version: "0.1.0" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/health"
    );
  });

  it("does not send a Content-Type header on a bodyless GET (avoids a CORS preflight)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await healthService.getHealth();

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.has("Content-Type")).toBe(false);
  });

  it("throws FetchError on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "boom" }, 500))
    );
    const err = await healthService.getHealth().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(FetchError);
    expect((err as FetchError).status).toBe(500);
  });

  it("rejects a schema-invalid body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ foo: 1 })));
    await expect(healthService.getHealth()).rejects.toThrow();
  });
});
