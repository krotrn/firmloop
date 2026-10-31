import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionsTab } from "@/components/versions/versions-tab";
import { ApiError } from "@/lib/fetcher";
import { CancelWaitingProvider } from "@/providers/cancel-waiting-provider";
import { deviceService, type DeviceStatus } from "@/services/device.service";
import type { Run } from "@/services/runs.service";
import * as service from "@/services/session-versions.service";
import { sessionsService } from "@/services/sessions.service";
import type { Version } from "@/services/versions.service";

vi.mock("@/hooks/use-events", () => ({ useEvents: vi.fn() }));
vi.mock("monaco-editor/editor/editor.api", () => ({}));
vi.mock("monaco-editor/languages/definitions/cpp/register.js", () => ({}));
vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  default: () => null,
  DiffEditor: () => <div data-testid="diff" />,
}));

const version = (number: number, overrides: Partial<Version> = {}): Version =>
  ({
    id: `id${number}`,
    number,
    session_id: "s1",
    status: "promoted",
    target_kind: "real",
    tests_total: 3 + number,
    promoted_at: "2026-10-01T10:00:00Z",
    parent_version_id: number > 1 ? `id${number - 1}` : null,
    spec: { summary: `Spec ${number}`, rules: [], scenarios: [] },
    firmware: { id: "fw" },
    ...overrides,
  }) as unknown as Version;

const device = (overrides: Partial<DeviceStatus> = {}): DeviceStatus =>
  ({
    busy: false,
    connected: true,
    mode: "virtual",
    running_version: null,
    ...overrides,
  }) as DeviceStatus;

const session = (currentNumber: number | null) =>
  ({
    id: "s1",
    title: "Fan",
    current_version:
      currentNumber === null
        ? null
        : { id: `id${currentNumber}`, number: currentNumber },
  }) as Awaited<ReturnType<typeof sessionsService.getSession>>;

const setup = (
  versions: Version[],
  opts: {
    current?: number | null;
    device?: DeviceStatus;
    devicePromise?: Promise<DeviceStatus>;
  } = {}
) => {
  vi.spyOn(service, "listSessionVersions").mockResolvedValue({
    items: versions,
    next_cursor: null,
  });
  vi.spyOn(sessionsService, "getSession").mockResolvedValue(
    session(opts.current === undefined ? null : opts.current)
  );
  vi.spyOn(deviceService, "getDevice").mockImplementation(
    () => opts.devicePromise ?? Promise.resolve(opts.device ?? device())
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <CancelWaitingProvider>
        <VersionsTab sessionId="s1" />
      </CancelWaitingProvider>
    </QueryClientProvider>
  );
  return { invalidate };
};

const row = async (n: number) => {
  const heading = await screen.findByText(`v${n}`, { selector: "span" });
  return heading.closest("li") as HTMLElement;
};

describe("VersionsTab", () => {
  beforeEach(() => {
    vi.spyOn(sessionsService, "getSession").mockResolvedValue(session(null));
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an empty state", async () => {
    setup([]);
    expect(
      await screen.findByText(/No verified versions yet/)
    ).toBeInTheDocument();
  });

  it("lists versions with summary, tests and kind badges", async () => {
    setup([version(2, { target_kind: "virtual" }), version(1)]);
    const r2 = await row(2);
    expect(within(r2).getByText("Spec 2")).toBeInTheDocument();
    expect(within(r2).getByText("5 tests")).toBeInTheDocument();
    expect(within(r2).getByText("Virtual")).toBeInTheDocument();
    expect(within(await row(1)).getByText("Real")).toBeInTheDocument();
  });

  it("badges the current version and the version on the board", async () => {
    setup([version(2), version(1)], {
      current: 2,
      device: device({
        running_version: {
          id: "id1",
          number: 1,
          session_id: "s1",
          session_title: "Fan",
        },
      }),
    });
    expect(within(await row(2)).getByText("Current")).toBeInTheDocument();
    expect(within(await row(2)).queryByText("On board")).toBeNull();
    expect(within(await row(1)).getByText("On board")).toBeInTheDocument();
  });

  it("loads more versions", async () => {
    const list = vi
      .spyOn(service, "listSessionVersions")
      .mockResolvedValueOnce({ items: [version(2)], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [version(1)], next_cursor: null });
    vi.spyOn(deviceService, "getDevice").mockResolvedValue(device());
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CancelWaitingProvider>
          <VersionsTab sessionId="s1" />
        </CancelWaitingProvider>
      </QueryClientProvider>
    );
    await row(2);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await row(1);
    expect(list).toHaveBeenLastCalledWith("s1", {
      status: "promoted",
      cursor: "c1",
    });
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("opens the diff dialog against the current version", async () => {
    const diffSpy = vi.spyOn(service, "diffVersions").mockResolvedValue({
      code_old: "a",
      code_new: "b",
      spec_diff: { summary_changed: false, rules: {}, scenarios: {} },
    });
    setup([version(3), version(2), version(1)], { current: 2 });
    await userEvent.click(
      within(await row(3)).getByRole("button", { name: "Diff" })
    );
    expect(await screen.findByTestId("diff")).toBeInTheDocument();
    expect(diffSpy).toHaveBeenCalledWith("id3", "id2");
  });

  it("hides Roll back for the version that is current and on the board", async () => {
    setup([version(2), version(1)], {
      current: 2,
      device: device({
        running_version: {
          id: "id2",
          number: 2,
          session_id: "s1",
          session_title: "Fan",
        },
      }),
    });
    expect(
      within(await row(2)).queryByRole("button", { name: "Roll back" })
    ).toBeNull();
    expect(
      within(await row(1)).getByRole("button", { name: "Roll back" })
    ).toBeEnabled();
  });

  it("keeps Roll back for a current version that is not on the board", async () => {
    setup([version(2)], { current: 2 });
    expect(
      within(await row(2)).getByRole("button", { name: "Roll back" })
    ).toBeEnabled();
  });

  it("disables Roll back with the reason for a virtual-only version in real mode", async () => {
    setup([version(1, { target_kind: "virtual", firmware: null })], {
      device: device({ mode: "real" }),
    });
    const r = await row(1);
    expect(within(r).getByRole("button", { name: "Roll back" })).toBeDisabled();
    expect(
      within(r).getByText("Verified virtually only — no firmware to flash")
    ).toBeInTheDocument();
  });

  it("keeps Roll back disabled until the device status has loaded", async () => {
    let resolve: (status: DeviceStatus) => void = () => {};
    const pending = new Promise<DeviceStatus>((r) => {
      resolve = r;
    });
    setup([version(1, { target_kind: "virtual", firmware: null })], {
      devicePromise: pending,
    });
    const button = within(await row(1)).getByRole("button", {
      name: "Roll back",
    });
    expect(button).toBeDisabled();
    resolve(device());
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("allows Roll back of a virtual-only version in virtual mode", async () => {
    setup([version(1, { target_kind: "virtual", firmware: null })]);
    expect(
      within(await row(1)).getByRole("button", { name: "Roll back" })
    ).toBeEnabled();
  });

  it("confirms, rolls back and refetches the session", async () => {
    const rollback = vi
      .spyOn(service, "rollbackVersion")
      .mockResolvedValue({ id: "run" } as Run);
    const { invalidate } = setup([version(2), version(1)]);
    await userEvent.click(
      within(await row(1)).getByRole("button", { name: "Roll back" })
    );
    expect(
      await screen.findByRole("heading", { name: "Roll back to v1?" })
    ).toBeInTheDocument();
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Roll back",
      })
    );
    await waitFor(() => expect(rollback).toHaveBeenCalledWith("id1", false));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] })
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("offers to cancel a waiting run and resends", async () => {
    const rollback = vi
      .spyOn(service, "rollbackVersion")
      .mockRejectedValueOnce(
        new ApiError(409, "RUN_WAITING", "waiting", { session_id: "s2" })
      )
      .mockResolvedValueOnce({ id: "run" } as Run);
    setup([version(2), version(1)]);
    await userEvent.click(
      within(await row(1)).getByRole("button", { name: "Roll back" })
    );
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Roll back",
      })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(rollback).toHaveBeenLastCalledWith("id1", true));
  });

  it("shows the error under the row", async () => {
    vi.spyOn(service, "rollbackVersion").mockRejectedValue(
      new ApiError(409, "RUN_ACTIVE", "A run is already active", {})
    );
    setup([version(2), version(1)]);
    const r = await row(1);
    await userEvent.click(within(r).getByRole("button", { name: "Roll back" }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Roll back",
      })
    );
    expect(await within(r).findByRole("alert")).toHaveTextContent(
      "A run is already active"
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
