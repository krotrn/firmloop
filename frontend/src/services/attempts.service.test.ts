import { afterEach, describe, expect, it, vi } from "vitest";

import { listRunAttempts } from "@/services/attempts.service";

describe("attempts service", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the attempts of a run and unwraps the items", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [{ id: "a1", number: 1 }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(listRunAttempts("r 1")).resolves.toEqual([
      { id: "a1", number: 1 },
    ]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/runs/r%201/attempts"
    );
  });
});
