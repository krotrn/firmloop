import { afterEach, describe, expect, it, vi } from "vitest";

import { manualRun, retryRun } from "@/services/run-actions.service";

const stub = () => {
  const fetchMock = vi.fn().mockImplementation(
    () =>
      new Response(JSON.stringify({ id: "new" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const call = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls[0] as [string, RequestInit];

describe("run action services", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs manual code with cancel_waiting defaulting to false", async () => {
    const fetchMock = stub();
    await expect(manualRun("r 1", "void user_setup() {}")).resolves.toEqual({
      id: "new",
    });
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r%201/manual");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      code: "void user_setup() {}",
      cancel_waiting: false,
    });
  });

  it("POSTs the retry spec with cancel_waiting", async () => {
    const fetchMock = stub();
    const spec = { summary: "s", rules: [], scenarios: [] };
    await retryRun("r1", spec, true);
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r1/retry");
    expect(JSON.parse(init.body as string)).toEqual({
      spec,
      cancel_waiting: true,
    });
  });
});
