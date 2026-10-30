import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetcher, isApiError } from "@/lib/fetcher";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(response: Response) {
  const mock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("fetcher", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed JSON body on success", async () => {
    stubFetch(jsonResponse({ ok: true }));
    await expect(fetcher("/x")).resolves.toEqual({ ok: true });
  });

  it("returns undefined on 204", async () => {
    stubFetch(new Response(null, { status: 204 }));
    await expect(fetcher("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("sets Content-Type only when there is a body", async () => {
    const mock = vi.fn().mockImplementation(async () => jsonResponse({}));
    vi.stubGlobal("fetch", mock);
    await fetcher("/x");
    await fetcher("/x", { method: "POST", body: "{}" });
    const get = new Headers((mock.mock.calls[0]?.[1] as RequestInit).headers);
    const post = new Headers((mock.mock.calls[1]?.[1] as RequestInit).headers);
    expect(get.has("Content-Type")).toBe(false);
    expect(post.get("Content-Type")).toBe("application/json");
  });

  it("throws ApiError with code, message and details from the error body", async () => {
    stubFetch(
      jsonResponse(
        {
          error: {
            code: "RUN_WAITING",
            message: "a run is waiting",
            details: { session_id: "s1" },
          },
        },
        409
      )
    );
    const err = await fetcher("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe("RUN_WAITING");
    expect(apiErr.message).toBe("a run is waiting");
    expect(apiErr.details).toEqual({ session_id: "s1" });
  });

  it.each([
    [
      "an HTML body",
      new Response("<html>bad gateway</html>", {
        status: 502,
        statusText: "Bad Gateway",
      }),
    ],
    [
      "an empty body",
      new Response("", { status: 500, statusText: "Server Error" }),
    ],
    ["a wrong shape", jsonResponse({ detail: "boom" }, 500)],
    [
      "an unknown code",
      jsonResponse({ error: { code: "NOPE", message: "m", details: {} } }, 500),
    ],
    [
      "a missing details",
      jsonResponse({ error: { code: "NOT_FOUND", message: "m" } }, 404),
    ],
  ])("maps %s to INTERNAL with the HTTP status", async (_name, response) => {
    stubFetch(response);
    const err = (await fetcher("/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("INTERNAL");
    expect(err.status).toBe(response.status);
    expect(err.details).toEqual({});
  });

  it.each([
    ["an empty body", ""],
    ["a non-JSON body", "<html></html>"],
  ])("throws ApiError INTERNAL for a 200 with %s", async (_name, text) => {
    stubFetch(new Response(text, { status: 200 }));
    const err = (await fetcher("/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("INTERNAL");
    expect(err.status).toBe(200);
  });

  it("uses the status text as the message for a non-conforming body", async () => {
    stubFetch(new Response("x", { status: 502, statusText: "Bad Gateway" }));
    const err = (await fetcher("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe("Bad Gateway");
  });
});

describe("isApiError", () => {
  const err = new ApiError(409, "RUN_WAITING", "m", {});

  it("matches any ApiError, or one with a given code", () => {
    expect(isApiError(err)).toBe(true);
    expect(isApiError(err, "RUN_WAITING")).toBe(true);
    expect(isApiError(err, "RUN_ACTIVE")).toBe(false);
  });

  it("rejects other values", () => {
    expect(isApiError(new Error("x"))).toBe(false);
    expect(isApiError("x")).toBe(false);
    expect(isApiError(undefined)).toBe(false);
  });
});
