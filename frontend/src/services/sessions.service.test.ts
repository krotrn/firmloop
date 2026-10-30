import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/fetcher";
import { sessionsService, type Session } from "@/services/sessions.service";

const session: Session = {
  id: "s 1",
  title: "Fan control",
  archived: false,
  current_version: null,
  last_run: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("sessionsService.getSession", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the session by id and returns the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(session), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(sessionsService.getSession("s 1")).resolves.toEqual(session);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/sessions/s%201"
    );
  });

  it("throws ApiError when the session does not exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "NOT_FOUND", message: "no session", details: {} },
          }),
          { status: 404 }
        )
      )
    );
    const err = await sessionsService.getSession("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("NOT_FOUND");
  });
});
