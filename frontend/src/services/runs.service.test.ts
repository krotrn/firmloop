import { afterEach, describe, expect, it, vi } from "vitest";

import { runsService, type Run } from "@/services/runs.service";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const stub = (response: Response) => {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

describe("runsService", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists a session's runs without parameters", async () => {
    const fetchMock = stub(json({ items: [], next_cursor: null }));
    await expect(runsService.listSessionRuns("s 1")).resolves.toEqual({
      items: [],
      next_cursor: null,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toBe("http://127.0.0.1:8000/api/sessions/s%201/runs");
    expect(init?.method).toBeUndefined();
  });

  it("passes the cursor and limit, encoded", async () => {
    const fetchMock = stub(json({ items: [], next_cursor: null }));
    await runsService.listSessionRuns("s", { cursor: "a b/c", limit: 20 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/sessions/s/runs?cursor=a%20b%2Fc&limit=20"
    );
  });

  it("POSTs the request body to create a run", async () => {
    const fetchMock = stub(json({ id: "r1" } as Partial<Run>, 201));
    const body = {
      request_text: "blink",
      input_mode: "text" as const,
      cancel_waiting: true,
    };
    await expect(runsService.createRun("s 1", body)).resolves.toEqual({
      id: "r1",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/sessions/s%201/runs");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });

  it("POSTs to the cancel endpoint without a body", async () => {
    const fetchMock = stub(json({ id: "r 1" }));
    await runsService.cancelRun("r 1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r%201/cancel");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});
