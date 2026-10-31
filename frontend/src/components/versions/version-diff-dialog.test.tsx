import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  VersionDiffDialog,
  defaultAgainst,
} from "@/components/versions/version-diff-dialog";
import * as service from "@/services/session-versions.service";
import * as versionsService from "@/services/versions.service";
import type { Version } from "@/services/versions.service";

vi.mock("monaco-editor/editor/editor.api", () => ({}));
vi.mock("monaco-editor/languages/definitions/cpp/register.js", () => ({}));
vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  default: () => null,
  DiffEditor: (props: { original?: string; modified?: string }) => (
    <div
      data-testid="diff"
      data-original={props.original}
      data-modified={props.modified}
    />
  ),
}));

const version = (id: string, number: number, parent: string | null = null) =>
  ({ id, number, parent_version_id: parent }) as Version;

const v1 = version("id1", 1);
const v2 = version("id2", 2, "id1");
const v3 = version("id3", 3, "id2");

const diff = (): service.VersionDiff => ({
  code_old: "old code",
  code_new: "new code",
  spec_diff: {
    summary_changed: true,
    summary_old: "Fan on",
    summary_new: "Fan on when hot",
    rules: { added: ["r2"], modified: [], removed: [] },
    scenarios: { added: [], modified: ["sc1"], removed: ["sc9"] },
  },
});

const renderDialog = (
  props: Partial<React.ComponentProps<typeof VersionDiffDialog>> = {}
) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <VersionDiffDialog
        version={v3}
        versions={[v3, v2, v1]}
        currentVersion={{ id: "id2", number: 2 }}
        onClose={vi.fn()}
        {...props}
      />
    </QueryClientProvider>
  );

describe("defaultAgainst", () => {
  const current = { id: "id2", number: 2 };
  it("prefers the current version", () => {
    expect(defaultAgainst(v3, [v3, v2, v1], current)).toBe("id2");
  });
  it("uses the parent when this is the current version", () => {
    expect(defaultAgainst(v2, [v3, v2, v1], current)).toBe("id1");
  });
  it("uses the parent when there is no current version", () => {
    expect(defaultAgainst(v3, [v3, v2, v1], null)).toBe("id2");
  });
  it("falls back to the next older version", () => {
    expect(defaultAgainst(version("id3", 3), [v3, v2, v1], null)).toBe("id2");
  });
  it("is null when nothing else exists", () => {
    expect(defaultAgainst(v1, [v1], null)).toBeNull();
  });
});

describe("VersionDiffDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("compares with the current version by default and labels both sides", async () => {
    const spy = vi.spyOn(service, "diffVersions").mockResolvedValue(diff());
    renderDialog();
    expect(await screen.findByTestId("diff")).toHaveAttribute(
      "data-original",
      "old code"
    );
    expect(spy).toHaveBeenCalledWith("id3", "id2");
    expect(screen.getByTestId("diff")).toHaveAttribute(
      "data-modified",
      "new code"
    );
    expect(
      screen.getByRole("combobox", { name: "Compare with" })
    ).toHaveTextContent("v2");
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("v2");
    expect(dialog).toHaveTextContent("v3");
  });

  it("lists the spec changes in words", async () => {
    vi.spyOn(service, "diffVersions").mockResolvedValue(diff());
    renderDialog();
    expect(await screen.findByText("Fan on when hot")).toBeInTheDocument();
    expect(screen.getByText("Fan on")).toBeInTheDocument();
    const rules = screen.getByRole("group", { name: "Rules" });
    expect(rules).toHaveTextContent("Added");
    expect(rules).toHaveTextContent("r2");
    const scenarios = screen.getByRole("group", { name: "Scenarios" });
    expect(scenarios).toHaveTextContent("Changed");
    expect(scenarios).toHaveTextContent("sc1");
    expect(scenarios).toHaveTextContent("Removed");
    expect(scenarios).toHaveTextContent("sc9");
  });

  it("says when the spec did not change", async () => {
    vi.spyOn(service, "diffVersions").mockResolvedValue({
      code_old: "a",
      code_new: "b",
      spec_diff: {
        summary_changed: false,
        rules: {},
        scenarios: {},
      },
    });
    renderDialog();
    expect(await screen.findByText("No spec changes.")).toBeInTheDocument();
  });

  it("requests the diff again when another version is chosen", async () => {
    const spy = vi.spyOn(service, "diffVersions").mockResolvedValue(diff());
    renderDialog();
    await screen.findByTestId("diff");
    await userEvent.click(
      screen.getByRole("combobox", { name: "Compare with" })
    );
    await userEvent.click(await screen.findByRole("option", { name: "v1" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("id3", "id1"));
  });

  it("shows an error when the diff fails", async () => {
    vi.spyOn(service, "diffVersions").mockRejectedValue(new Error("boom"));
    renderDialog();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("names versions outside the loaded pages by their number", async () => {
    vi.spyOn(service, "diffVersions").mockResolvedValue(diff());
    const lookup = vi
      .spyOn(versionsService, "getVersion")
      .mockResolvedValue(version("id9", 9));
    renderDialog({
      version: version("id10", 10, "id9"),
      versions: [version("id10", 10, "id9")],
      currentVersion: null,
    });
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Compare with" })
      ).toHaveTextContent("v9")
    );
    expect(lookup).toHaveBeenCalledWith("id9");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Changes from v9 to v10."
    );
  });

  it("describes the two sides", async () => {
    vi.spyOn(service, "diffVersions").mockResolvedValue(diff());
    renderDialog();
    expect(
      await screen.findByText("Changes from v2 to v3.")
    ).toBeInTheDocument();
  });

  it("explains when there is nothing to compare with", () => {
    const spy = vi.spyOn(service, "diffVersions");
    renderDialog({ version: v1, versions: [v1], currentVersion: null });
    expect(
      screen.getByText("No other version to compare with.")
    ).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });
});
