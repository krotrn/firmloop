import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatWaitStep } from "@/components/run/chat-wait-step";

describe("ChatWaitStep", () => {
  it("renders nothing when no task ever waited", () => {
    const { container } = render(
      <ChatWaitStep state="idle" runStatus="coding" />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a running step while waiting", () => {
    render(<ChatWaitStep state="waiting" runStatus="coding" />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("Waiting for chat");
    expect(item).toHaveTextContent("In progress");
    expect(item).toHaveAttribute("aria-current", "step");
  });

  it("shows done after the answer", () => {
    render(<ChatWaitStep state="answered" runStatus="coding" />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("Done");
    expect(item).not.toHaveAttribute("aria-current");
  });

  it("never shows a running step once the run ended", () => {
    const { container } = render(
      <ChatWaitStep state="waiting" runStatus="cancelled" />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
