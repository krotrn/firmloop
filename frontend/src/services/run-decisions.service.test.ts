import { afterEach, describe, expect, it, vi } from "vitest";

import {
  answerRun,
  confirmRun,
  rejectRun,
} from "@/services/run-decisions.service";
import type { Run } from "@/services/runs.service";

const stub = () => {
  const fetchMock = vi.fn().mockImplementation(
    () =>
      new Response(JSON.stringify({ id: "r 1" } as Partial<Run>), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const call = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls[0] as [string, RequestInit];

describe("run decision services", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the answer", async () => {
    const fetchMock = stub();
    await expect(answerRun("r 1", "21 C")).resolves.toEqual({ id: "r 1" });
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r%201/answer");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ answer: "21 C" });
  });

  it("confirms with an empty body when no spec is given", async () => {
    const fetchMock = stub();
    await confirmRun("r1");
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r1/confirm");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("confirms with the edited spec", async () => {
    const fetchMock = stub();
    const spec = { summary: "s", rules: [], scenarios: [] };
    await confirmRun("r1", spec);
    expect(JSON.parse(call(fetchMock)[1].body as string)).toEqual({ spec });
  });

  it("POSTs the reject feedback", async () => {
    const fetchMock = stub();
    await rejectRun("r1", "too slow");
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/runs/r1/reject");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ feedback: "too slow" });
  });
});
