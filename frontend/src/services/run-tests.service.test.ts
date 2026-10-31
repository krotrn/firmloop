import { afterEach, describe, expect, it, vi } from "vitest";

import { listRunTests } from "@/services/run-tests.service";

describe("run tests service", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the test cases of a run and unwraps the items", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [{ id: "t1" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(listRunTests("r 1")).resolves.toEqual([{ id: "t1" }]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/runs/r%201/tests"
    );
  });
});
