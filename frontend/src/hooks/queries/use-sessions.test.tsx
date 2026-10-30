import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  sessionsQueryKeys,
  useArchiveSession,
  useCreateSession,
  useSession,
  useSessions,
  useUpdateSession,
} from "@/hooks/queries/use-sessions";
import { ApiError } from "@/lib/fetcher";
import { sessionsService, type Session } from "@/services/sessions.service";

const make = (id: string): Session => ({
  id,
  title: id,
  archived: false,
  current_version: null,
  last_run: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const setup = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
};

describe("sessions hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("exposes the list key", () => {
    expect(sessionsQueryKeys.all).toEqual(["sessions"]);
  });

  it("pages with next_cursor until it is null", async () => {
    const list = vi
      .spyOn(sessionsService, "listSessions")
      .mockResolvedValueOnce({ items: [make("a")], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [make("b")], next_cursor: null });
    const { wrapper } = setup();
    const { result } = renderHook(() => useSessions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    expect(list).toHaveBeenLastCalledWith({ cursor: null });

    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(list).toHaveBeenLastCalledWith({ cursor: "c1" });
    await waitFor(() =>
      expect(result.current.data?.pages.flatMap((p) => p.items)).toHaveLength(2)
    );
    expect(result.current.hasNextPage).toBe(false);
  });

  it("invalidates the list after creating a session", async () => {
    vi.spyOn(sessionsService, "createSession").mockResolvedValue(make("n"));
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateSession(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions"] });
  });

  it("exposes the detail key", () => {
    expect(sessionsQueryKeys.detail("s1")).toEqual(["sessions", "s1"]);
  });

  it("loads one session", async () => {
    const get = vi
      .spyOn(sessionsService, "getSession")
      .mockResolvedValue(make("s1"));
    const { wrapper } = setup();
    const { result } = renderHook(() => useSession("s1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(get).toHaveBeenCalledWith("s1");
    expect(result.current.data?.id).toBe("s1");
  });

  it("does not retry a 404", async () => {
    const get = vi
      .spyOn(sessionsService, "getSession")
      .mockRejectedValue(new ApiError(404, "NOT_FOUND", "no session"));
    const queryClient = new QueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useSession("x"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("sets the detail data and invalidates the list after a rename", async () => {
    const renamed = { ...make("s1"), title: "New title" };
    const update = vi
      .spyOn(sessionsService, "updateSession")
      .mockResolvedValue(renamed);
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useUpdateSession("s1"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ title: "New title" });
    });
    expect(update).toHaveBeenCalledWith("s1", { title: "New title" });
    expect(queryClient.getQueryData(["sessions", "s1"])).toEqual(renamed);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions"] });
  });

  it("archives and invalidates the list", async () => {
    const archive = vi
      .spyOn(sessionsService, "archiveSession")
      .mockResolvedValue(undefined);
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useArchiveSession("s1"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(archive).toHaveBeenCalledWith("s1");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["sessions"],
      exact: true,
    });
  });
});
