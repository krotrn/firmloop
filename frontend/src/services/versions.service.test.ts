import { afterEach, describe, expect, it, vi } from "vitest";

import { getVersion } from "@/services/versions.service";

describe("versions service", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches one version", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "v 1", code: "int x;" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(getVersion("v 1")).resolves.toEqual({
      id: "v 1",
      code: "int x;",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/versions/v%201"
    );
  });
});
