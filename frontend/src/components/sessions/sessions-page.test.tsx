import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsPage } from "@/components/sessions/sessions-page";
import { ApiError } from "@/lib/fetcher";
import { sessionsService, type Session } from "@/services/sessions.service";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const make = (id: string, over: Partial<Session> = {}): Session => ({
  id,
  title: `Session ${id}`,
  archived: false,
  current_version: null,
  last_run: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
  ...over,
});

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionsPage />
    </QueryClientProvider>
  );
};

describe("SessionsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    push.mockReset();
  });

  it("shows skeleton rows while loading", () => {
    vi.spyOn(sessionsService, "listSessions").mockReturnValue(
      new Promise(() => {})
    );
    renderPage();
    expect(screen.getByRole("heading", { name: "Sessions" })).toBeVisible();
    expect(
      screen.getByRole("status", { name: "Loading sessions" })
    ).toBeVisible();
  });

  it("renders title link, version, status and relative time", async () => {
    const updated = new Date(Date.now() - 3 * 60 * 1000).toISOString();
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [
        make("a", {
          title: "Fan control",
          current_version: { id: "v1", number: 4 },
          last_run: { id: "r1", status: "succeeded" },
          updated_at: updated,
        }),
      ],
      next_cursor: null,
    });
    renderPage();

    const link = await screen.findByRole("link", { name: /Fan control/ });
    expect(link).toHaveAttribute("href", "/sessions/a");
    expect(screen.getByText("v4")).toBeVisible();
    expect(screen.getByText("Succeeded")).toBeVisible();
    const time = screen.getByText("3 minutes ago");
    expect(time).toHaveAttribute("title", new Date(updated).toLocaleString());
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("shows a dash and No runs for null version and run", async () => {
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [make("a")],
      next_cursor: null,
    });
    renderPage();
    expect(await screen.findByText("—")).toBeVisible();
    expect(screen.getByText("No runs")).toBeVisible();
  });

  it("loads more with the cursor and appends", async () => {
    const list = vi
      .spyOn(sessionsService, "listSessions")
      .mockResolvedValueOnce({ items: [make("a")], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [make("b")], next_cursor: null });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Load more" })
    );
    expect(await screen.findByText("Session b")).toBeVisible();
    expect(screen.getByText("Session a")).toBeVisible();
    expect(list).toHaveBeenLastCalledWith({ cursor: "c1" });
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("shows the empty state with a New session button", async () => {
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    renderPage();
    expect(await screen.findByText("No sessions yet")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "New session" })).toHaveLength(
      1
    );
  });

  it("shows the error with Retry that refetches", async () => {
    const list = vi
      .spyOn(sessionsService, "listSessions")
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "Backend exploded"))
      .mockResolvedValueOnce({ items: [make("a")], next_cursor: null });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Backend exploded"
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Session a")).toBeVisible();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("creates a session, disables the button while pending, then navigates", async () => {
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [make("a")],
      next_cursor: null,
    });
    let resolve!: (s: Session) => void;
    const create = vi
      .spyOn(sessionsService, "createSession")
      .mockReturnValue(new Promise<Session>((r) => (resolve = r)));
    renderPage();
    await screen.findByText("Session a");

    const button = screen.getByRole("button", { name: "New session" });
    await userEvent.click(button);
    expect(create).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(button).toBeDisabled());

    resolve(make("new 1"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/sessions/new%201"));
  });

  it("shows the error inline when creating fails", async () => {
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [make("a")],
      next_cursor: null,
    });
    vi.spyOn(sessionsService, "createSession").mockRejectedValue(
      new ApiError(409, "INTERNAL", "Cannot create right now")
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "New session" })
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cannot create right now"
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("keeps the rows and shows an inline error when Load more fails", async () => {
    const list = vi
      .spyOn(sessionsService, "listSessions")
      .mockResolvedValueOnce({ items: [make("a")], next_cursor: "c1" })
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "Page failed"))
      .mockResolvedValueOnce({ items: [make("b")], next_cursor: null });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Load more" })
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Page failed");
    expect(screen.getByText("Session a")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Session b")).toBeVisible();
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("ignores a second click after success and posts once", async () => {
    vi.spyOn(sessionsService, "listSessions").mockResolvedValue({
      items: [make("a")],
      next_cursor: null,
    });
    const create = vi
      .spyOn(sessionsService, "createSession")
      .mockResolvedValue(make("n 2"));
    renderPage();
    await screen.findByText("Session a");

    const button = screen.getByRole("button", { name: "New session" });
    await userEvent.click(button);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/sessions/n%202"));
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(create).toHaveBeenCalledTimes(1);
    expect(push).toHaveBeenCalledTimes(1);
  });
});
