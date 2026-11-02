import { render, screen } from "@testing-library/react";
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
});
