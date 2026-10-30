import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SessionPage from "@/app/sessions/[id]/page";
import { SessionWorkspace } from "@/components/workspace/session-workspace";
import { ApiError } from "@/lib/fetcher";
import { CancelWaitingProvider } from "@/providers/cancel-waiting-provider";
import { EventsProvider } from "@/providers/events-provider";
import { deviceService, type DeviceStatus } from "@/services/device.service";
import { hardwareService } from "@/services/hardware.service";
import { runsService, type Run } from "@/services/runs.service";
import { sessionsService, type Session } from "@/services/sessions.service";
import { FakeWebSocket } from "../../../tests/fake-websocket";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const TS = "2026-01-01T00:00:00Z";

const session = (over: Partial<Session> = {}): Session => ({
  id: "s1",
  title: "Fan control",
  archived: false,
  current_version: { id: "v1", number: 3 },
  last_run: null,
  created_at: TS,
  updated_at: TS,
  ...over,
});

const run = (id: string, over: Partial<Run> = {}): Run =>
  ({
    id,
    session_id: "s1",
    kind: "request",
    input_mode: "text",
    request_text: `Request ${id}`,
    status: "succeeded",
    started_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    finished_at: null,
    result_version: null,
    base_version: null,
    failure: null,
    attempts: [],
    ...over,
  }) as unknown as Run;

const virtualDevice: DeviceStatus = {
  mode: "virtual",
  connected: true,
  busy: false,
  running_version: null,
};

function mockData({
  sessionData = session(),
  runs = [] as Run[],
  device = virtualDevice,
}: {
  sessionData?: Session;
  runs?: Run[];
  device?: DeviceStatus;
} = {}) {
  return {
    getSession: vi
      .spyOn(sessionsService, "getSession")
      .mockResolvedValue(sessionData),
    listRuns: vi
      .spyOn(runsService, "listSessionRuns")
      .mockResolvedValue({ items: runs, next_cursor: null }),
    getDevice: vi.spyOn(deviceService, "getDevice").mockResolvedValue(device),
  };
}

function renderWorkspace(id = "s1") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <CancelWaitingProvider>
          <SessionWorkspace id={id} />
        </CancelWaitingProvider>
      </EventsProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const textbox = () => screen.getByRole("textbox", { name: "Request" });
const sendButton = () => screen.getByRole("button", { name: "Send" });

describe("SessionWorkspace header", () => {
  it("shows a skeleton, then the title, version and device line", async () => {
    mockData();
    renderWorkspace();
    expect(
      screen.getByRole("status", { name: "Loading session" })
    ).toBeVisible();

    expect(
      await screen.findByRole("heading", { name: /Fan control/ })
    ).toBeVisible();
    expect(screen.getByText("v3")).toBeVisible();
    expect(await screen.findByText("virtual")).toBeVisible();
    expect(screen.getByText("connected")).toBeVisible();
    expect(screen.getByText("Board: no program")).toBeVisible();
  });

  it("shows No version yet and the port only in real mode", async () => {
    mockData({
      sessionData: session({ current_version: null }),
      device: {
        mode: "real",
        busy: false,
        connected: false,
        port: "/dev/ttyUSB0",
        running_version: null,
      },
    });
    renderWorkspace();
    expect(await screen.findByText("No version yet")).toBeVisible();
    expect(await screen.findByText("real")).toBeVisible();
    expect(screen.getByText("disconnected")).toBeVisible();
    expect(screen.getByText("/dev/ttyUSB0")).toBeVisible();
  });

  it("does not show the port in virtual mode", async () => {
    mockData({ device: { ...virtualDevice, port: "/dev/ttyUSB0" } });
    renderWorkspace();
    await screen.findByText("virtual");
    expect(screen.queryByText("/dev/ttyUSB0")).toBeNull();
  });

  it("shows a muted message when the device status fails", async () => {
    mockData();
    vi.spyOn(deviceService, "getDevice").mockRejectedValue(
      new ApiError(500, "INTERNAL", "boom")
    );
    renderWorkspace();
    expect(await screen.findByText("Device status unavailable")).toBeVisible();
    expect(screen.getByRole("heading", { name: /Fan control/ })).toBeVisible();
  });

  it("updates the device line from a device.status event", async () => {
    mockData();
    renderWorkspace();
    await screen.findByText("virtual");

    act(() => {
      FakeWebSocket.last.receive({
        type: "device.status",
        ts: TS,
        status: { mode: "real", connected: true, port: "/dev/ttyACM0" },
      });
    });
    expect(await screen.findByText("real")).toBeVisible();
    expect(screen.getByText("/dev/ttyACM0")).toBeVisible();
  });
});

describe("SessionWorkspace load states", () => {
  it("shows Session not found with a link back on 404", async () => {
    mockData();
    vi.spyOn(sessionsService, "getSession").mockRejectedValue(
      new ApiError(404, "NOT_FOUND", "no session")
    );
    renderWorkspace();
    expect(
      await screen.findByRole("heading", { name: "Session not found" })
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to sessions" })
    ).toHaveAttribute("href", "/");
  });

  it("shows the message with Retry on other errors", async () => {
    const data = mockData();
    data.getSession
      .mockRejectedValueOnce(new ApiError(400, "VALIDATION", "Bad request"))
      .mockResolvedValueOnce(session());
    renderWorkspace();
    expect(await screen.findByRole("alert")).toHaveTextContent("Bad request");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("heading", { name: /Fan control/ })
    ).toBeVisible();
  });
});

describe("board-owner notice", () => {
  it("names another session's version and links to it", async () => {
    mockData({
      device: {
        ...virtualDevice,
        running_version: {
          id: "v9",
          number: 2,
          session_id: "s2",
          session_title: "Heater",
        },
      },
    });
    renderWorkspace();
    const text = await screen.findByText(/Board runs/);
    expect(text).toHaveAttribute("role", "status");
    expect(text).toHaveTextContent(
      "Board runs Heater v2. The next deploy will replace it."
    );
    expect(screen.getByRole("link", { name: "Heater v2" })).toHaveAttribute(
      "href",
      "/sessions/s2"
    );
  });

  it("falls back to another session when the title and number are missing", async () => {
    mockData({
      device: {
        ...virtualDevice,
        running_version: {
          id: "v9",
          number: null,
          session_id: "s2",
          session_title: null,
        },
      },
    });
    renderWorkspace();
    expect(await screen.findByText(/Board runs/)).toHaveTextContent(
      "Board runs another session. The next deploy will replace it."
    );
  });

  it("is hidden when the board runs this session or nothing", async () => {
    mockData({
      device: {
        ...virtualDevice,
        running_version: {
          id: "v1",
          number: 3,
          session_id: "s1",
          session_title: "Fan control",
        },
      },
    });
    renderWorkspace();
    await screen.findByText("virtual");
    expect(screen.queryByText(/Board runs/)).toBeNull();
  });
});

describe("request box", () => {
  it("sends a text request, clears the box and follows the newest run", async () => {
    mockData();
    const create = vi
      .spyOn(runsService, "createRun")
      .mockResolvedValue(run("r1"));
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    expect(textbox()).toHaveAttribute(
      "placeholder",
      "What should the board do?"
    );
    expect(sendButton()).toBeDisabled();
    await userEvent.type(textbox(), "  blink the led  ");
    await userEvent.click(sendButton());

    await waitFor(() => expect(textbox()).toHaveValue(""));
    expect(create).toHaveBeenCalledWith("s1", {
      request_text: "blink the led",
      input_mode: "text",
      cancel_waiting: false,
    });
  });

  it("sends with Ctrl+Enter", async () => {
    mockData();
    const create = vi
      .spyOn(runsService, "createRun")
      .mockResolvedValue(run("r1"));
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    await userEvent.type(textbox(), "go{Control>}{Enter}{/Control}");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  });

  it("keeps the text and shows the message when the run is rejected", async () => {
    mockData();
    vi.spyOn(runsService, "createRun").mockRejectedValue(
      new ApiError(409, "RUN_ACTIVE", "A run is already active")
    );
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    await userEvent.type(textbox(), "go");
    await userEvent.click(sendButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A run is already active"
    );
    expect(textbox()).toHaveValue("go");
  });

  it("keeps the text without an error when the user keeps waiting", async () => {
    mockData();
    const create = vi.spyOn(runsService, "createRun").mockRejectedValue(
      new ApiError(409, "RUN_WAITING", "A run is waiting", {
        session_id: "s2",
      })
    );
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    await userEvent.type(textbox(), "go");
    await userEvent.click(sendButton());
    await userEvent.click(
      await screen.findByRole("button", { name: "Keep waiting" })
    );

    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(textbox()).toHaveValue("go");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("resends with cancel_waiting after the user confirms", async () => {
    mockData();
    const create = vi
      .spyOn(runsService, "createRun")
      .mockRejectedValueOnce(
        new ApiError(409, "RUN_WAITING", "A run is waiting", {
          session_id: "s2",
        })
      )
      .mockResolvedValueOnce(run("r1"));
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    await userEvent.type(textbox(), "go");
    await userEvent.click(sendButton());
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(textbox()).toHaveValue(""));
    expect(create).toHaveBeenLastCalledWith(
      "s1",
      expect.objectContaining({ cancel_waiting: true })
    );
  });

  it("is disabled with a hint while the latest run is working", async () => {
    mockData({ runs: [run("r2", { status: "coding" }), run("r1")] });
    renderWorkspace();
    expect(await screen.findByText("A run is in progress.")).toBeVisible();
    await userEvent.type(textbox(), "more");
    expect(sendButton()).toBeDisabled();
  });

  it("is enabled when the latest run is waiting for the user", async () => {
    mockData({ runs: [run("r1", { status: "awaiting_answer" })] });
    renderWorkspace();
    await screen.findByRole("list");
    await userEvent.type(textbox(), "answer");
    expect(sendButton()).toBeEnabled();
    expect(screen.queryByText("A run is in progress.")).toBeNull();
  });

  it("shows the counter only above 1800 characters and blocks above 2000", async () => {
    mockData();
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });

    fireChange(textbox(), "a".repeat(1800));
    expect(screen.queryByText(/\/2000/)).toBeNull();
    fireChange(textbox(), "a".repeat(1801));
    expect(screen.getByText("1801/2000")).toBeVisible();
    expect(sendButton()).toBeEnabled();
    fireChange(textbox(), "a".repeat(2001));
    expect(screen.getByText("2001/2000")).toBeVisible();
    expect(sendButton()).toBeDisabled();
  });
});

function fireChange(element: HTMLElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value"
  )?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("run history and run panel", () => {
  const runs = [
    run("r4", { status: "building" }),
    run("r3", {
      kind: "rollback",
      request_text: null,
      base_version: { id: "v1", number: 2 },
      status: "succeeded",
      result_version: { id: "v5", number: 5, status: "promoted" },
    }),
    run("r2", { kind: "manual", request_text: null, status: "failed" }),
    run("r1", { status: "cancelled" }),
  ];

  it("labels request, rollback and manual runs, newest first", async () => {
    mockData({ runs });
    renderWorkspace();
    const list = await screen.findByRole("list");
    const rows = within(list).getAllByRole("button");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("Request r4");
    expect(rows[1]).toHaveTextContent("Rollback to v2");
    expect(rows[1]).toHaveTextContent("v5");
    expect(rows[2]).toHaveTextContent("Manual edit");
    expect(rows[3]).toHaveTextContent("Request r1");
    expect(within(rows[3]!).getByText("5 minutes ago")).toHaveAttribute(
      "title",
      new Date(runs[3]!.started_at).toLocaleString()
    );
  });

  it("labels a rollback without version numbers", async () => {
    mockData({
      runs: [run("r1", { kind: "rollback", request_text: null })],
    });
    renderWorkspace();
    const list = await screen.findByRole("list");
    expect(within(list).getByRole("button")).toHaveTextContent(/^Rollback/);
    expect(within(list).getByRole("button")).not.toHaveTextContent("to v");
  });

  it("shows the empty message when there are no runs", async () => {
    mockData();
    renderWorkspace();
    expect(
      await screen.findByText("No runs yet. Describe what the board should do.")
    ).toBeVisible();
  });

  it("selects the newest run and pins an older one until the newest is clicked", async () => {
    mockData({ runs });
    vi.spyOn(runsService, "createRun").mockResolvedValue(run("r5"));
    renderWorkspace();
    const list = await screen.findByRole("list");
    const row = (name: string) =>
      within(list).getByRole("button", { name: new RegExp(name) });

    expect(row("Request r4")).toHaveAttribute("aria-current", "true");

    await userEvent.click(row("Request r1"));
    expect(row("Request r1")).toHaveAttribute("aria-current", "true");
    expect(row("Request r4")).not.toHaveAttribute("aria-current");

    await userEvent.click(row("Request r4"));
    expect(row("Request r4")).toHaveAttribute("aria-current", "true");
  });

  it("returns to the newest run after a send", async () => {
    const finished = [run("r2"), run("r1")];
    mockData({ runs: finished });
    vi.spyOn(runsService, "createRun").mockResolvedValue(run("r3"));
    renderWorkspace();
    const list = await screen.findByRole("list");
    await userEvent.click(
      within(list).getByRole("button", { name: /Request r1/ })
    );
    expect(
      within(list).getByRole("button", { name: /Request r1/ })
    ).toHaveAttribute("aria-current", "true");

    await userEvent.type(textbox(), "next");
    await userEvent.click(sendButton());
    await waitFor(() =>
      expect(
        within(list).getByRole("button", { name: /Request r2/ })
      ).toHaveAttribute("aria-current", "true")
    );
  });

  it("loads more runs with the cursor", async () => {
    const data = mockData();
    data.listRuns
      .mockResolvedValueOnce({ items: [run("r2")], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [run("r1")], next_cursor: null });
    renderWorkspace();
    await userEvent.click(
      await screen.findByRole("button", { name: "Load more" })
    );
    expect(await screen.findByRole("list")).toHaveTextContent("Request r1");
    expect(data.listRuns).toHaveBeenLastCalledWith("s1", { cursor: "c1" });
  });

  it("shows an error with Retry when the runs fail", async () => {
    const data = mockData();
    data.listRuns
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "Runs exploded"))
      .mockResolvedValueOnce({ items: [run("r1")], next_cursor: null });
    renderWorkspace();
    expect(await screen.findByText("Runs exploded")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("list")).toHaveTextContent("Request r1");
  });

  it("shows the selected run's summary with Cancel while it is not terminal", async () => {
    mockData({ runs: [run("r1", { status: "coding" })] });
    const cancel = vi
      .spyOn(runsService, "cancelRun")
      .mockResolvedValue(run("r1", { status: "cancelled" }));
    renderWorkspace();

    const tab = await screen.findByRole("tab", { name: "Run" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    const cancelButton = await screen.findByRole("button", { name: "Cancel" });
    await userEvent.click(cancelButton);
    expect(cancel).toHaveBeenCalledWith("r1");
  });

  it("shows failure details and no Cancel for a failed run", async () => {
    mockData({
      runs: [
        run("r1", {
          status: "failed",
          finished_at: new Date().toISOString(),
          failure: {
            code: "BUILD_FAILED",
            message: "Compile error",
            details: {},
          },
        }),
      ],
    });
    renderWorkspace();
    expect(await screen.findByText("Compile error")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.getByText("Finished")).toBeVisible();
  });

  it("explains the other tabs", async () => {
    mockData();
    renderWorkspace();
    await screen.findByRole("tab", { name: "Code" });
    await userEvent.click(screen.getByRole("tab", { name: "Code" }));
    expect(
      await screen.findByText("Code appears here once a run writes it.")
    ).toBeVisible();
    for (const name of ["Tests", "Versions", "Device"]) {
      expect(screen.getByRole("tab", { name })).toBeVisible();
    }
  });
});

describe("live updates", () => {
  const runStatus = (sessionId: string) => ({
    type: "run.status",
    ts: TS,
    run_id: "r1",
    session_id: sessionId,
    status: "coding",
    previous_status: null,
  });

  it("refetches runs for this session's events only", async () => {
    const data = mockData({ runs: [run("r1", { status: "pending" })] });
    renderWorkspace();
    await screen.findByRole("list");
    const runCalls = data.listRuns.mock.calls.length;
    const sessionCalls = data.getSession.mock.calls.length;

    act(() => FakeWebSocket.last.receive(runStatus("other")));
    await act(async () => {});
    expect(data.listRuns.mock.calls.length).toBe(runCalls);

    data.listRuns.mockResolvedValue({
      items: [run("r1", { status: "coding" })],
      next_cursor: null,
    });
    act(() => FakeWebSocket.last.receive(runStatus("s1")));
    await waitFor(() =>
      expect(data.listRuns.mock.calls.length).toBeGreaterThan(runCalls)
    );
    expect(data.getSession.mock.calls.length).toBeGreaterThan(sessionCalls);
    expect(await screen.findAllByText("Coding")).not.toHaveLength(0);
  });

  it("subscribes to the session", async () => {
    mockData();
    renderWorkspace();
    await screen.findByRole("heading", { name: /Fan control/ });
    act(() => FakeWebSocket.last.open());
    expect(FakeWebSocket.last.sentMessages).toContainEqual({
      type: "subscribe",
      session_id: "s1",
    });
  });
});

describe("rename", () => {
  const open = async () => {
    await userEvent.click(
      await screen.findByRole("button", { name: /Rename session/ })
    );
    return screen.getByRole("textbox", { name: "Session title" });
  };

  it("saves a changed title with Enter", async () => {
    mockData();
    const update = vi
      .spyOn(sessionsService, "updateSession")
      .mockResolvedValue(session({ title: "Heater" }));
    renderWorkspace();
    const input = await open();
    vi.spyOn(sessionsService, "getSession").mockResolvedValue(
      session({ title: "Heater" })
    );
    expect(input).toHaveValue("Fan control");

    await userEvent.clear(input);
    await userEvent.type(input, "  Heater  {Enter}");
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("s1", { title: "Heater" })
    );
    expect(
      await screen.findByRole("heading", { name: /Heater/ })
    ).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Session title" })).toBeNull();
  });

  it("saves on blur", async () => {
    mockData();
    const update = vi
      .spyOn(sessionsService, "updateSession")
      .mockResolvedValue(session({ title: "Heater" }));
    renderWorkspace();
    const input = await open();
    await userEvent.clear(input);
    await userEvent.type(input, "Heater");
    await userEvent.tab();
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  });

  it("cancels with Escape without saving", async () => {
    mockData();
    const update = vi.spyOn(sessionsService, "updateSession");
    renderWorkspace();
    const input = await open();
    await userEvent.type(input, "zzz{Escape}");
    expect(screen.queryByRole("textbox", { name: "Session title" })).toBeNull();
    expect(screen.getByRole("heading", { name: /Fan control/ })).toBeVisible();
    expect(update).not.toHaveBeenCalled();
  });

  it("does not save an empty or unchanged title", async () => {
    mockData();
    const update = vi.spyOn(sessionsService, "updateSession");
    renderWorkspace();
    let input = await open();
    await userEvent.clear(input);
    await userEvent.type(input, "   {Enter}");
    expect(screen.queryByRole("textbox", { name: "Session title" })).toBeNull();

    input = await open();
    await userEvent.type(input, "{Enter}");
    expect(update).not.toHaveBeenCalled();
  });

  it("shows a save error inline and keeps editing", async () => {
    mockData();
    vi.spyOn(sessionsService, "updateSession").mockRejectedValue(
      new ApiError(422, "VALIDATION", "Title is invalid")
    );
    renderWorkspace();
    const input = await open();
    await userEvent.type(input, "!{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Title is invalid"
    );
    expect(screen.getByRole("textbox", { name: "Session title" })).toHaveValue(
      "Fan control!"
    );
  });
});

describe("rename retry", () => {
  it("does not resend a failed value on a later blur", async () => {
    mockData();
    const update = vi
      .spyOn(sessionsService, "updateSession")
      .mockRejectedValue(new ApiError(422, "VALIDATION", "Title is invalid"));
    renderWorkspace();
    await userEvent.click(
      await screen.findByRole("button", { name: /Rename session/ })
    );
    const input = screen.getByRole("textbox", { name: "Session title" });
    await userEvent.type(input, "!{Enter}");
    await screen.findByRole("alert");
    expect(update).toHaveBeenCalledTimes(1);

    await userEvent.tab();
    await userEvent.click(input);
    await userEvent.tab();
    expect(update).toHaveBeenCalledTimes(1);

    await userEvent.click(input);
    await userEvent.type(input, "?{Enter}");
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  });
});

describe("archive", () => {
  const openDialog = async () => {
    await userEvent.click(
      await screen.findByRole("button", { name: "Session actions" })
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Archive session" })
    );
    return screen.findByRole("dialog");
  };

  it("confirms, archives and returns to the sessions list", async () => {
    mockData();
    const archive = vi
      .spyOn(sessionsService, "archiveSession")
      .mockResolvedValue(undefined);
    renderWorkspace();
    const dialog = await openDialog();
    expect(within(dialog).getByText("Archive this session?")).toBeVisible();
    expect(
      within(dialog).getByText(
        "It leaves the sessions list. Its versions stay."
      )
    ).toBeVisible();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Archive" })
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(archive).toHaveBeenCalledWith("s1");
  });

  it("does nothing when cancelled", async () => {
    mockData();
    const archive = vi.spyOn(sessionsService, "archiveSession");
    renderWorkspace();
    const dialog = await openDialog();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" })
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(archive).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows the error in the dialog and stays on the page", async () => {
    mockData();
    vi.spyOn(sessionsService, "archiveSession").mockRejectedValue(
      new ApiError(500, "INTERNAL", "Could not archive")
    );
    renderWorkspace();
    const dialog = await openDialog();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Archive" })
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not archive"
    );
    expect(push).not.toHaveBeenCalled();
  });
});

describe("switching sessions", () => {
  it("resets the draft and the selection when the id changes", async () => {
    mockData({ runs: [run("r2"), run("r1")] });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const tree = async (id: string) => (
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <CancelWaitingProvider>
            {await SessionPage({ params: Promise.resolve({ id }) })}
          </CancelWaitingProvider>
        </EventsProvider>
      </QueryClientProvider>
    );
    const { rerender } = render(await tree("s1"));
    const list = await screen.findByRole("list");
    await userEvent.type(textbox(), "draft");
    await userEvent.click(
      within(list).getByRole("button", { name: /Request r1/ })
    );

    mockData({
      sessionData: session({ id: "s2", title: "Heater" }),
      runs: [run("r2"), run("r1")],
    });
    rerender(await tree("s2"));
    expect(
      await screen.findByRole("heading", { name: /Heater/ })
    ).toBeVisible();
    expect(textbox()).toHaveValue("");
    // Both runs are still listed, so only a fresh mount explains the selection moving back.
    const runs = within(await screen.findByRole("list"));
    expect(runs.getByRole("button", { name: /Request r2/ })).toHaveAttribute(
      "aria-current",
      "true"
    );
    expect(
      runs.getByRole("button", { name: /Request r1/ })
    ).not.toHaveAttribute("aria-current");
  });

  it("follows the newest run when the pinned run leaves the list", async () => {
    const data = mockData({ runs: [run("r2"), run("r1")] });
    renderWorkspace();
    const list = await screen.findByRole("list");
    await userEvent.click(
      within(list).getByRole("button", { name: /Request r1/ })
    );

    data.listRuns.mockResolvedValue({ items: [run("r2")], next_cursor: null });
    act(() =>
      FakeWebSocket.last.receive({
        type: "run.status",
        ts: TS,
        run_id: "r2",
        session_id: "s1",
        status: "succeeded",
        previous_status: null,
      })
    );
    await waitFor(() =>
      expect(screen.getAllByRole("listitem")).toHaveLength(1)
    );

    data.listRuns.mockResolvedValue({
      items: [run("r2"), run("r1")],
      next_cursor: null,
    });
    act(() =>
      FakeWebSocket.last.receive({
        type: "run.status",
        ts: TS,
        run_id: "r2",
        session_id: "s1",
        status: "succeeded",
        previous_status: null,
      })
    );
    await waitFor(() =>
      expect(screen.getAllByRole("listitem")).toHaveLength(2)
    );
    expect(
      within(screen.getByRole("list")).getByRole("button", {
        name: /Request r2/,
      })
    ).toHaveAttribute("aria-current", "true");
  });
});

describe("spec card and device tab", () => {
  beforeEach(() => {
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue({
      components: [],
      channels: [
        {
          id: "temp",
          kind: "sensor",
          type: "float",
          unit: "°C",
          range: [-10, 50],
          resolution: 0.1,
          neutral: 20,
          default_hysteresis: null,
          injectable: true,
        },
      ],
    });
  });

  it("shows the selected run's question in the left column", async () => {
    mockData({
      runs: [
        run("r1", {
          status: "awaiting_answer",
          question: { question: "Which fan?", reason: "Two are wired." },
        }),
      ],
    });
    renderWorkspace();
    expect(await screen.findByText("Which fan?")).toBeVisible();
  });

  it("shows the device panel for the board's mode in the Device tab", async () => {
    mockData();
    renderWorkspace();
    await userEvent.click(await screen.findByRole("tab", { name: "Device" }));
    expect(
      await screen.findByText("Waiting for telemetry…")
    ).toBeInTheDocument();
    expect((await screen.findAllByLabelText("temp")).length).toBeGreaterThan(0);
  });
});
