import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  CHAT_CONNECT_COMMAND,
  ChatConnect,
} from "@/components/settings/chat-connect";

describe("ChatConnect", () => {
  it("shows the connect command and the next step", () => {
    render(<ChatConnect />);
    expect(CHAT_CONNECT_COMMAND).toBe(
      "claude mcp add --transport http firmloop http://127.0.0.1:8000/mcp"
    );
    expect(screen.getByText(CHAT_CONNECT_COMMAND)).toBeVisible();
    expect(
      screen.getByText(
        "Then ask Claude Code to build something; this page shows the run live."
      )
    ).toBeVisible();
  });

  it("copies the command", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    render(<ChatConnect />);
    await user.click(screen.getByRole("button", { name: "Copy command" }));
    expect(writeText).toHaveBeenCalledWith(CHAT_CONNECT_COMMAND);
    expect(await screen.findByText("Copied")).toBeVisible();
  });

  it("clears the copied label after a moment", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
      render(<ChatConnect />);
      await userEvent
        .setup({ advanceTimers: vi.advanceTimersByTime })
        .click(screen.getByRole("button", { name: "Copy command" }));
      expect(await screen.findByText("Copied")).toBeVisible();
      act(() => vi.advanceTimersByTime(2100));
      expect(screen.queryByText("Copied")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so when the clipboard write is rejected", async () => {
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new Error("denied")
    );
    render(<ChatConnect />);
    await userEvent.click(screen.getByRole("button", { name: "Copy command" }));
    expect(await screen.findByText("Copy failed")).toBeVisible();
  });
});
