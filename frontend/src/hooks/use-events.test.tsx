import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEvents, useRunEvents } from "@/hooks/use-events";
import { EventsProvider } from "@/providers/events-provider";
import type { ServerEvent } from "@/types/generated/events";
import { FakeWebSocket } from "../../tests/fake-websocket";

const TS = "2026-01-01T00:00:00Z";

const runStatus = (sessionId: string, status = "testing"): ServerEvent => ({
  type: "run.status",
  ts: TS,
  run_id: "r1",
  session_id: sessionId,
  status,
  previous_status: null,
});

const attemptStarted = (number: number, sessionId = "s1"): ServerEvent => ({
  type: "attempt.started",
  ts: TS,
  run_id: "r1",
  session_id: sessionId,
  attempt_id: `a${number}`,
  number,
});

const telemetry: ServerEvent = {
  type: "device.telemetry",
  ts: TS,
  mode: "run",
  t: 1,
  s: {},
  a: {},
  d: ["", "", "", ""],
};

function Wrapper({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
}

const subscribes = (socket: FakeWebSocket) =>
  socket.sentMessages.filter((m) => m.type === "subscribe");

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useRunEvents", () => {
  it("sends subscribe once the socket is open", () => {
    renderHook(() => useRunEvents("s1"), { wrapper: Wrapper });
    const socket = FakeWebSocket.last;
    expect(socket.sentMessages).toEqual([]);

    act(() => socket.open());
    expect(socket.sentMessages).toEqual([
      { type: "subscribe", session_id: "s1" },
    ]);
  });

  it("subscribes immediately when the socket is already open", () => {
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useRunEvents(id),
      { wrapper: Wrapper, initialProps: { id: null as string | null } }
    );
    act(() => FakeWebSocket.last.open());
    expect(subscribes(FakeWebSocket.last)).toEqual([]);

    rerender({ id: "s1" });
    expect(subscribes(FakeWebSocket.last)).toEqual([
      { type: "subscribe", session_id: "s1" },
    ]);
  });

  it("returns run events oldest first and reports the connection status", () => {
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    expect(result.current.status).toBe("connecting");
    act(() => FakeWebSocket.last.open());
    expect(result.current.status).toBe("open");

    act(() => {
      FakeWebSocket.last.receive(runStatus("s1", "coding"));
      FakeWebSocket.last.receive(attemptStarted(1));
    });
    expect(result.current.events.map((e) => e.type)).toEqual([
      "run.status",
      "attempt.started",
    ]);
  });

  it("keeps deploy and verify events", () => {
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    act(() => {
      FakeWebSocket.last.receive({
        type: "deploy.build",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        ok: true,
      });
      FakeWebSocket.last.receive({
        type: "verify.check",
        ts: TS,
        run_id: "r1",
        session_id: "s1",
        kind: "hello",
        status: "passed",
        details: {},
      });
    });
    expect(result.current.events.map((e) => e.type)).toEqual([
      "deploy.build",
      "verify.check",
    ]);
  });

  it("does not include device events", () => {
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    act(() => FakeWebSocket.last.receive(telemetry));
    expect(result.current.events).toEqual([]);
  });

  it("drops run events that belong to another session", () => {
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    act(() => {
      FakeWebSocket.last.receive(runStatus("other"));
      FakeWebSocket.last.receive({
        type: "run.created",
        ts: TS,
        run: { id: "r9", session_id: "other" },
      });
      FakeWebSocket.last.receive(attemptStarted(1, "other"));
      FakeWebSocket.last.receive(runStatus("s1"));
    });
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0]).toMatchObject({ session_id: "s1" });
  });

  it("keeps only the last 1000 events", () => {
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    act(() => {
      for (let n = 1; n <= 1005; n++)
        FakeWebSocket.last.receive(attemptStarted(n));
    });
    expect(result.current.events).toHaveLength(1000);
    expect(result.current.events[0]).toMatchObject({ number: 6 });
    expect(result.current.events.at(-1)).toMatchObject({ number: 1005 });
  });

  it("clears events and subscribes again when the id changes", () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useRunEvents(id),
      { wrapper: Wrapper, initialProps: { id: "s1" as string | null } }
    );
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.receive(runStatus("s1")));
    expect(result.current.events).toHaveLength(1);

    rerender({ id: "s2" });
    expect(result.current.events).toEqual([]);
    expect(subscribes(FakeWebSocket.last).at(-1)).toEqual({
      type: "subscribe",
      session_id: "s2",
    });

    act(() => FakeWebSocket.last.receive(runStatus("s1")));
    expect(result.current.events).toEqual([]);
  });

  it("shows no events when returning to an earlier id", () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useRunEvents(id),
      { wrapper: Wrapper, initialProps: { id: "s1" as string | null } }
    );
    act(() => FakeWebSocket.last.receive(runStatus("s1")));
    expect(result.current.events).toHaveLength(1);

    rerender({ id: "s2" });
    rerender({ id: "s1" });
    expect(result.current.events).toEqual([]);

    rerender({ id: null });
    rerender({ id: "s1" });
    expect(result.current.events).toEqual([]);
  });

  it("unsubscribes when the id becomes null", () => {
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useRunEvents(id),
      { wrapper: Wrapper, initialProps: { id: "s1" as string | null } }
    );
    act(() => FakeWebSocket.last.open());
    rerender({ id: null });
    expect(FakeWebSocket.last.sentMessages.at(-1)).toEqual({
      type: "unsubscribe",
    });
  });

  it("sends unsubscribe when the component unmounts", () => {
    function Consumer() {
      useRunEvents("s1");
      return null;
    }
    function Page({ show }: { show: boolean }) {
      return <EventsProvider>{show ? <Consumer /> : null}</EventsProvider>;
    }
    const client = new QueryClient();
    const tree = (show: boolean) => (
      <QueryClientProvider client={client}>
        <Page show={show} />
      </QueryClientProvider>
    );
    const { rerender } = render(tree(true));
    act(() => FakeWebSocket.last.open());
    rerender(tree(false));
    expect(FakeWebSocket.last.sentMessages.at(-1)).toEqual({
      type: "unsubscribe",
    });
    expect(FakeWebSocket.last.closed).toBe(false);
  });

  it("does not unsubscribe when another hook took over the subscription", () => {
    const { rerender } = renderHook(
      ({ ids }: { ids: string[] }) => {
        useRunEvents(ids[0] ?? null);
        useRunEvents(ids[1] ?? null);
      },
      { wrapper: Wrapper, initialProps: { ids: ["s1", "s2"] } }
    );
    act(() => FakeWebSocket.last.open());
    // s2 was subscribed last, so it holds the subscription; dropping the s1 hook must
    // not cancel it.
    rerender({ ids: [] });
    const unsubscribes = FakeWebSocket.last.sentMessages.filter(
      (m) => m.type === "unsubscribe"
    );
    expect(unsubscribes).toHaveLength(1);
  });

  it("keeps the subscription until the last consumer of a session unmounts", () => {
    function Consumer() {
      useRunEvents("s1");
      return null;
    }
    function Page({ count }: { count: number }) {
      return (
        <EventsProvider>
          {Array.from({ length: count }, (_, i) => (
            <Consumer key={i} />
          ))}
        </EventsProvider>
      );
    }
    const client = new QueryClient();
    const tree = (count: number) => (
      <QueryClientProvider client={client}>
        <Page count={count} />
      </QueryClientProvider>
    );
    const { rerender } = render(tree(2));
    act(() => FakeWebSocket.last.open());
    const unsubscribes = () =>
      FakeWebSocket.last.sentMessages.filter((m) => m.type === "unsubscribe");

    rerender(tree(1));
    expect(unsubscribes()).toHaveLength(0);
    rerender(tree(0));
    expect(unsubscribes()).toHaveLength(1);
  });

  it("subscribes again after a reconnect", () => {
    renderHook(() => useRunEvents("s1"), { wrapper: Wrapper });
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => FakeWebSocket.last.open());
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(subscribes(FakeWebSocket.last)).toEqual([
      { type: "subscribe", session_id: "s1" },
    ]);
  });

  it("ignores an invalid message and warns", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { result } = renderHook(() => useRunEvents("s1"), {
      wrapper: Wrapper,
    });
    act(() => {
      FakeWebSocket.last.receive("{broken");
      FakeWebSocket.last.receive({ type: "run.status", ts: TS });
    });
    expect(result.current.events).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });
});

describe("in StrictMode", () => {
  it("keeps one live socket and subscribes once", () => {
    function Consumer() {
      useRunEvents("s1");
      return null;
    }
    const client = new QueryClient();
    render(
      <React.StrictMode>
        <QueryClientProvider client={client}>
          <EventsProvider>
            <Consumer />
          </EventsProvider>
        </QueryClientProvider>
      </React.StrictMode>
    );

    const live = FakeWebSocket.instances.filter((s) => !s.closed);
    expect(live).toHaveLength(1);
    expect(FakeWebSocket.instances.filter((s) => s.closed)).toHaveLength(
      FakeWebSocket.instances.length - 1
    );
    act(() => live[0].open());
    expect(subscribes(live[0])).toEqual([
      { type: "subscribe", session_id: "s1" },
    ]);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeWebSocket.instances.filter((s) => !s.closed)).toHaveLength(1);
    expect(subscribes(live[0])).toHaveLength(1);
  });
});

describe("useEvents", () => {
  it("passes every validated event to the handler, including device events", () => {
    const handler = vi.fn();
    renderHook(() => useEvents(handler), { wrapper: Wrapper });
    act(() => {
      FakeWebSocket.last.receive(telemetry);
      FakeWebSocket.last.receive(runStatus("anything"));
    });
    expect(handler.mock.calls.map(([e]) => (e as ServerEvent).type)).toEqual([
      "device.telemetry",
      "run.status",
    ]);
  });

  it("calls the latest handler without resubscribing", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ h }) => useEvents(h), {
      wrapper: Wrapper,
      initialProps: { h: first },
    });
    rerender({ h: second });
    act(() => FakeWebSocket.last.receive(telemetry));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("keeps delivering to other listeners when one throws", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const second = vi.fn();
    renderHook(
      () => {
        useEvents(() => {
          throw new Error("boom");
        });
        useEvents(second);
      },
      { wrapper: Wrapper }
    );
    act(() => FakeWebSocket.last.receive(telemetry));
    expect(second).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledTimes(1);
  });

  it("does not call the handler for an invalid message", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const handler = vi.fn();
    renderHook(() => useEvents(handler), { wrapper: Wrapper });
    act(() => FakeWebSocket.last.receive({ type: "nope" }));
    expect(handler).not.toHaveBeenCalled();
  });
});
