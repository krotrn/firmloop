import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { wsUrl } from "@/lib/events";
import {
  EventsProvider,
  useEventsConnection,
} from "@/providers/events-provider";
import { FakeWebSocket } from "../../tests/fake-websocket";

let queryClient: QueryClient;

function Status() {
  const { status } = useEventsConnection();
  return <div data-testid="status">{status}</div>;
}

function renderProvider() {
  return render(
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <Status />
      </EventsProvider>
    </QueryClientProvider>
  );
}

const status = () => screen.getByTestId("status").textContent;

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  queryClient = new QueryClient();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("EventsProvider", () => {
  it("connects to the WebSocket endpoint and reports its status", () => {
    renderProvider();
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.last.url).toBe(wsUrl());
    expect(status()).toBe("connecting");

    act(() => FakeWebSocket.last.open());
    expect(status()).toBe("open");

    act(() => FakeWebSocket.last.drop());
    expect(status()).toBe("closed");
  });

  it("reconnects after 1, 2, 4, 8 and 8 seconds", () => {
    renderProvider();
    const delays = [1000, 2000, 4000, 8000, 8000];
    delays.forEach((delay, i) => {
      act(() => FakeWebSocket.last.drop());
      act(() => {
        vi.advanceTimersByTime(delay - 1);
      });
      expect(FakeWebSocket.instances).toHaveLength(i + 1);
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(FakeWebSocket.instances).toHaveLength(i + 2);
    });
  });

  it("starts again at one second after a successful open", () => {
    renderProvider();
    act(() => FakeWebSocket.last.drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => FakeWebSocket.last.drop());
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(FakeWebSocket.instances).toHaveLength(3);

    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeWebSocket.instances).toHaveLength(4);
  });

  it("invalidates queries after a reconnect but not on the first connect", () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderProvider();
    act(() => FakeWebSocket.last.open());
    expect(invalidate).not.toHaveBeenCalled();

    act(() => FakeWebSocket.last.drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => FakeWebSocket.last.open());
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("sends a ping every 25 seconds while open and stops when closed", () => {
    renderProvider();
    const socket = FakeWebSocket.last;
    act(() => socket.open());
    act(() => {
      vi.advanceTimersByTime(24_999);
    });
    expect(socket.sentMessages).toEqual([]);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(socket.sentMessages).toEqual([{ type: "ping" }]);
    act(() => {
      vi.advanceTimersByTime(25_000);
    });
    expect(socket.sentMessages).toHaveLength(2);

    act(() => socket.drop());
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(socket.sentMessages).toHaveLength(2);
  });

  it("closes the socket on unmount and does not reconnect", () => {
    const { unmount } = renderProvider();
    const socket = FakeWebSocket.last;
    act(() => socket.open());
    unmount();
    expect(socket.closed).toBe(true);

    // A close event that arrives after unmount must not schedule a reconnect.
    socket.drop();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("clears a pending reconnect on unmount", () => {
    const { unmount } = renderProvider();
    act(() => FakeWebSocket.last.drop());
    unmount();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("throws when the hook is used outside the provider", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Status />)).toThrow(/EventsProvider/);
    error.mockRestore();
  });
});
