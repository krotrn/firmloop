import { afterEach, describe, expect, it, vi } from "vitest";

import {
  diffVersions,
  listSessionVersions,
  rollbackVersion,
} from "@/services/session-versions.service";

const stub = (body: unknown, status = 200) => {
  const fetchMock = vi.fn().mockImplementation(
    () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const call = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls[0] as [string, RequestInit | undefined];

describe("session versions service", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists promoted versions", async () => {
    const fetchMock = stub({ items: [], next_cursor: null });
    await listSessionVersions("s 1", { status: "promoted" });
    expect(call(fetchMock)[0]).toBe(
      "http://127.0.0.1:8000/api/sessions/s%201/versions?status=promoted"
    );
  });

  it("passes the cursor", async () => {
    const fetchMock = stub({ items: [], next_cursor: null });
    await listSessionVersions("s1", { status: "promoted", cursor: "a b" });
    expect(call(fetchMock)[0]).toBe(
      "http://127.0.0.1:8000/api/sessions/s1/versions?status=promoted&cursor=a%20b"
    );
  });

  it("requests a diff against another version", async () => {
    const fetchMock = stub({ code_old: "", code_new: "" });
    await diffVersions("v 2", "v1");
    expect(call(fetchMock)[0]).toBe(
      "http://127.0.0.1:8000/api/versions/v%202/diff?against=v1"
    );
  });

  it("POSTs a rollback with cancel_waiting defaulting to false", async () => {
    const fetchMock = stub({ id: "run" }, 202);
    await expect(rollbackVersion("v1")).resolves.toEqual({ id: "run" });
    const [url, init] = call(fetchMock);
    expect(url).toBe("http://127.0.0.1:8000/api/versions/v1/rollback");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ cancel_waiting: false });
  });

  it("sends cancel_waiting when asked", async () => {
    const fetchMock = stub({ id: "run" }, 202);
    await rollbackVersion("v1", true);
    expect(JSON.parse(call(fetchMock)[1]?.body as string)).toEqual({
      cancel_waiting: true,
    });
  });
});
