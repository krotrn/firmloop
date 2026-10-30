"use client";

import { useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { parseServerEvent, wsUrl } from "@/lib/events";
import type { ClientMessage, ServerEvent } from "@/types/generated/events";

export type ConnectionStatus = "connecting" | "open" | "closed";

type Listener = (event: ServerEvent) => void;

interface EventsContextValue {
  status: ConnectionStatus;
  /** Calls `listener` for every validated event; returns the function that removes it. */
  addListener: (listener: Listener) => () => void;
  /**
   * Makes `sessionId` the one subscription (the server keeps only one). The returned
   * function unsubscribes once the last consumer of that session is gone, unless a
   * different session has replaced it.
   */
  subscribeSession: (sessionId: string) => () => void;
}

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000];
const PING_INTERVAL_MS = 25_000;

const EventsContext = React.createContext<EventsContextValue | null>(null);

export function EventsProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = React.useState<ConnectionStatus>("connecting");

  const listenersRef = React.useRef(new Set<Listener>());
  // Consumers of the same session share one subscription; `users` counts them.
  const subscriptionRef = React.useRef<{
    sessionId: string;
    users: number;
  } | null>(null);
  // Set by the connection effect so the stable callbacks below can send on the live socket.
  const sendRef = React.useRef<(message: ClientMessage) => void>(() => {});

  React.useEffect(() => {
    let socket: WebSocket | null = null;
    let isOpen = false;
    let disposed = false;
    let hasOpened = false;
    let failures = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let pingTimer: ReturnType<typeof setInterval> | undefined;

    const send = (message: ClientMessage) => {
      if (socket && isOpen) socket.send(JSON.stringify(message));
    };
    sendRef.current = send;

    const connect = () => {
      const ws = new WebSocket(wsUrl());
      socket = ws;
      setStatus("connecting");

      ws.onopen = () => {
        isOpen = true;
        failures = 0;
        setStatus("open");
        pingTimer = setInterval(() => send({ type: "ping" }), PING_INTERVAL_MS);
        if (subscriptionRef.current !== null) {
          send({
            type: "subscribe",
            session_id: subscriptionRef.current.sessionId,
          });
        }
        // Events are not replayed, so whatever was missed while offline is refetched.
        if (hasOpened) void queryClient.invalidateQueries();
        hasOpened = true;
      };

      ws.onmessage = (message) => {
        if (typeof message.data !== "string") return;
        const event = parseServerEvent(message.data);
        if (!event) return;
        listenersRef.current.forEach((listener) => {
          try {
            listener(event);
          } catch (error) {
            console.error("WebSocket event listener failed", error);
          }
        });
      };

      ws.onclose = () => {
        isOpen = false;
        clearInterval(pingTimer);
        if (disposed) return;
        setStatus("closed");
        const delay =
          RECONNECT_DELAYS_MS[
            Math.min(failures, RECONNECT_DELAYS_MS.length - 1)
          ];
        failures += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      disposed = true;
      isOpen = false;
      clearTimeout(reconnectTimer);
      clearInterval(pingTimer);
      sendRef.current = () => {};
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.close();
      }
    };
  }, [queryClient]);

  const addListener = React.useCallback((listener: Listener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  const subscribeSession = React.useCallback((sessionId: string) => {
    let subscription = subscriptionRef.current;
    if (subscription?.sessionId === sessionId) {
      subscription.users += 1;
    } else {
      subscription = { sessionId, users: 1 };
      subscriptionRef.current = subscription;
      sendRef.current({ type: "subscribe", session_id: sessionId });
    }
    const mine = subscription;
    return () => {
      // A different session may have taken over since; that is not ours to cancel.
      if (subscriptionRef.current !== mine) return;
      mine.users -= 1;
      if (mine.users > 0) return;
      subscriptionRef.current = null;
      sendRef.current({ type: "unsubscribe" });
    };
  }, []);

  const value = React.useMemo(
    () => ({ status, addListener, subscribeSession }),
    [status, addListener, subscribeSession]
  );

  return (
    <EventsContext.Provider value={value}>{children}</EventsContext.Provider>
  );
}

export function useEventsConnection() {
  const context = React.useContext(EventsContext);
  if (!context) {
    throw new Error("useEventsConnection must be used inside EventsProvider");
  }
  return context;
}
