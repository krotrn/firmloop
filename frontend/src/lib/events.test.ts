import { afterEach, describe, expect, it, vi } from "vitest";

import { parseServerEvent, wsUrl } from "@/lib/events";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("parseServerEvent", () => {
  it("parses a hello event", () => {
    const raw = JSON.stringify({
      type: "hello",
      ts: "2026-01-01T00:00:00Z",
      server: "firmloop",
      proto: 1,
    });
    expect(parseServerEvent(raw)).toMatchObject({ type: "hello", proto: 1 });
  });

  it("parses a run.status event", () => {
    const raw = JSON.stringify({
      type: "run.status",
      ts: "2026-01-01T00:00:00Z",
      run_id: "r1",
      session_id: "s1",
      status: "testing",
      previous_status: "coding",
    });
    expect(parseServerEvent(raw)).toMatchObject({
      type: "run.status",
      run_id: "r1",
      session_id: "s1",
    });
  });

  it("parses a device.telemetry event", () => {
    const raw = JSON.stringify({
      type: "device.telemetry",
      ts: "2026-01-01T00:00:00Z",
      mode: "run",
      t: 1234,
      s: { temp: 21.5 },
      a: { fan: 0 },
      d: ["a", "b", "c", "d"],
    });
    expect(parseServerEvent(raw)).toMatchObject({
      type: "device.telemetry",
      mode: "run",
    });
  });

  it.each([
    ["an unknown type", JSON.stringify({ type: "nope", ts: "x" })],
    [
      "a missing required field",
      JSON.stringify({ type: "run.status", ts: "x", run_id: "r1" }),
    ],
    ["text that is not JSON", "{not json"],
    [
      "a client message",
      JSON.stringify({ type: "subscribe", session_id: "s" }),
    ],
  ])("returns null and warns for %s", (_name, raw) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(parseServerEvent(raw)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});

describe("wsUrl", () => {
  it("maps http to ws and appends /ws", () => {
    expect(wsUrl("http://127.0.0.1:8000")).toBe("ws://127.0.0.1:8000/ws");
  });

  it("maps https to wss", () => {
    expect(wsUrl("https://api.example.com/")).toBe("wss://api.example.com/ws");
  });

  it("defaults to the configured API url", () => {
    expect(wsUrl()).toMatch(/^wss?:\/\/.+\/ws$/);
  });
});
