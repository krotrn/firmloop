import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CodeDiff } from "@/components/code/code-diff";

vi.mock("monaco-editor/editor/editor.api", () => ({}));
vi.mock("monaco-editor/languages/definitions/cpp/register.js", () => ({}));
vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  default: () => null,
  DiffEditor: (props: {
    original?: string;
    modified?: string;
    options?: {
      renderSideBySide?: boolean;
      originalAriaLabel?: string;
      modifiedAriaLabel?: string;
    };
  }) => (
    <div
      data-testid="diff"
      data-original={props.original}
      data-modified={props.modified}
      data-side-by-side={String(props.options?.renderSideBySide)}
      data-original-label={props.options?.originalAriaLabel}
      data-modified-label={props.options?.modifiedAriaLabel}
    />
  ),
}));

const originalMatchMedia = window.matchMedia;

const narrowScreen = (narrow: boolean) => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: narrow && query.includes("max-width"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
};

describe("CodeDiff", () => {
  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("labels both sides and compares side by side on wide screens", async () => {
    narrowScreen(false);
    render(
      <CodeDiff
        original="a"
        modified="b"
        originalLabel="v2"
        modifiedLabel="Attempt 1"
      />
    );
    const diff = await screen.findByTestId("diff");
    expect(diff).toHaveAttribute("data-side-by-side", "true");
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1")).toBeInTheDocument();
  });

  it("switches to the inline view on narrow screens", async () => {
    narrowScreen(true);
    render(
      <CodeDiff
        original="a"
        modified="b"
        originalLabel="v2"
        modifiedLabel="Attempt 1"
      />
    );
    expect(await screen.findByTestId("diff")).toHaveAttribute(
      "data-side-by-side",
      "false"
    );
  });

  it("names both editors after their labels", async () => {
    narrowScreen(false);
    render(
      <CodeDiff
        original="a"
        modified="b"
        originalLabel="v2"
        modifiedLabel="Attempt 1"
      />
    );
    const diff = await screen.findByTestId("diff");
    expect(diff).toHaveAttribute("data-original-label", "v2");
    expect(diff).toHaveAttribute("data-modified-label", "Attempt 1");
  });
});
