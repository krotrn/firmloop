import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/fetcher";
import {
  CancelWaitingProvider,
  useCancelWaiting,
} from "@/providers/cancel-waiting-provider";
import * as sessionsService from "@/services/sessions.service";

type Send = (cancelWaiting: boolean) => Promise<string>;

const waitingError = () =>
  new ApiError(409, "RUN_WAITING", "a run is waiting", { session_id: "s1" });

function Harness({
  send,
  onDone,
}: {
  send: Send;
  onDone: (v: unknown) => void;
}) {
  const { runWithCancelWaiting } = useCancelWaiting();
  return (
    <button
      type="button"
      onClick={() => {
        runWithCancelWaiting(send).then(onDone, (e: unknown) => onDone(e));
      }}
    >
      go
    </button>
  );
}

function TwoCalls({
  sendA,
  sendB,
  onA,
  onB,
}: {
  sendA: Send;
  sendB: Send;
  onA: (v: unknown) => void;
  onB: (v: unknown) => void;
}) {
  const { runWithCancelWaiting } = useCancelWaiting();
  return (
    <button
      type="button"
      onClick={() => {
        void runWithCancelWaiting(sendA).then(onA);
        void runWithCancelWaiting(sendB).then(onB);
      }}
    >
      go
    </button>
  );
}

function setup(send: Send) {
  const onDone = vi.fn();
  const user = userEvent.setup();
  render(
    <CancelWaitingProvider>
      <Harness send={send} onDone={onDone} />
    </CancelWaitingProvider>
  );
  return { onDone, user };
}

describe("CancelWaitingProvider", () => {
  beforeEach(() => {
    vi.spyOn(sessionsService.sessionsService, "getSession").mockResolvedValue({
      id: "s1",
      title: "Fan control",
      archived: false,
      current_version: null,
      last_run: null,
      created_at: "",
      updated_at: "",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the result without a dialog when nothing is waiting", async () => {
    const send = vi.fn<Send>().mockResolvedValue("done");
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("done"));
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks first on RUN_WAITING and resends with cancel_waiting on confirm", async () => {
    const send = vi
      .fn<Send>()
      .mockRejectedValueOnce(waitingError())
      .mockResolvedValueOnce("second");
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));

    expect(await screen.findByText("Cancel waiting run?")).toBeInTheDocument();
    expect(
      screen.getByText(
        "A run in Fan control is waiting for you. Cancel it and continue?"
      )
    ).toBeInTheDocument();
    expect(sessionsService.sessionsService.getSession).toHaveBeenCalledWith(
      "s1"
    );

    await user.click(
      screen.getByRole("button", { name: "Cancel it and continue" })
    );

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("second"));
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith(true);
  });

  it("resolves undefined and sends nothing more on Keep waiting", async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(waitingError());
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));
    await user.click(
      await screen.findByRole("button", { name: "Keep waiting" })
    );

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(undefined));
    expect(send).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    );
  });

  it("falls back to 'another session' when the title cannot be fetched", async () => {
    vi.spyOn(sessionsService.sessionsService, "getSession").mockRejectedValue(
      new ApiError(404, "NOT_FOUND", "gone", {})
    );
    const send = vi.fn<Send>().mockRejectedValueOnce(waitingError());
    const { user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));

    expect(
      await screen.findByText(
        "A run in another session is waiting for you. Cancel it and continue?"
      )
    ).toBeInTheDocument();
  });

  it("resolves undefined when Escape closes the dialog", async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(waitingError());
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(undefined));
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("keeps the first caller waiting when a second confirmation replaces it", async () => {
    const sendA = vi.fn<Send>().mockRejectedValueOnce(waitingError());
    const sendB = vi
      .fn<Send>()
      .mockRejectedValueOnce(waitingError())
      .mockResolvedValueOnce("b-done");
    const onA = vi.fn();
    const onB = vi.fn();
    const user = userEvent.setup();
    render(
      <CancelWaitingProvider>
        <TwoCalls sendA={sendA} sendB={sendB} onA={onA} onB={onB} />
      </CancelWaitingProvider>
    );

    await user.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(onA).toHaveBeenCalledWith(undefined));
    expect(sendA).toHaveBeenCalledTimes(1);

    await user.click(
      await screen.findByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(onB).toHaveBeenCalledWith("b-done"));
    expect(sendB).toHaveBeenLastCalledWith(true);
  });

  it("lets the newest call win even when the older title loads last", async () => {
    let finishA: (title: string) => void = () => {};
    vi.mocked(sessionsService.sessionsService.getSession).mockImplementation(
      (id) =>
        new Promise((resolve) => {
          const session = {
            id,
            title: "",
            archived: false,
            current_version: null,
            last_run: null,
            created_at: "",
            updated_at: "",
          };
          const done = (title: string) => resolve({ ...session, title });
          if (id === "a") finishA = done;
          else done("Session B");
        })
    );
    const waitingIn = (sessionId: string) =>
      new ApiError(409, "RUN_WAITING", "a run is waiting", {
        session_id: sessionId,
      });
    const sendA = vi.fn<Send>().mockRejectedValueOnce(waitingIn("a"));
    const sendB = vi
      .fn<Send>()
      .mockRejectedValueOnce(waitingIn("b"))
      .mockResolvedValueOnce("b-done");
    const onA = vi.fn();
    const onB = vi.fn();
    const user = userEvent.setup();
    render(
      <CancelWaitingProvider>
        <TwoCalls sendA={sendA} sendB={sendB} onA={onA} onB={onB} />
      </CancelWaitingProvider>
    );

    await user.click(screen.getByRole("button", { name: "go" }));
    expect(await screen.findByText(/A run in Session B/)).toBeInTheDocument();
    finishA("Session A");

    await waitFor(() => expect(onA).toHaveBeenCalledWith(undefined));
    expect(sendA).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/A run in Session B/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(onB).toHaveBeenCalledWith("b-done"));
  });

  it("resolves an open confirmation as Keep waiting when unmounted", async () => {
    const send = vi.fn<Send>().mockRejectedValueOnce(waitingError());
    const onDone = vi.fn();
    const user = userEvent.setup();
    const { unmount } = render(
      <CancelWaitingProvider>
        <Harness send={send} onDone={onDone} />
      </CancelWaitingProvider>
    );

    await user.click(screen.getByRole("button", { name: "go" }));
    await screen.findByRole("dialog");
    unmount();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(undefined));
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("propagates errors from the confirmed resend", async () => {
    const failure = new ApiError(409, "RUN_ACTIVE", "busy", {});
    const send = vi
      .fn<Send>()
      .mockRejectedValueOnce(waitingError())
      .mockRejectedValueOnce(failure);
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));
    await user.click(
      await screen.findByRole("button", { name: "Cancel it and continue" })
    );

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(failure));
  });

  it("propagates other errors unchanged without a dialog", async () => {
    const failure = new ApiError(409, "RUN_ACTIVE", "busy", {});
    const send = vi.fn<Send>().mockRejectedValue(failure);
    const { onDone, user } = setup(send);

    await user.click(screen.getByRole("button", { name: "go" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(failure));
    expect(send).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sessionsService.sessionsService.getSession).not.toHaveBeenCalled();
  });
});
