import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RollbackDialog } from "@/components/versions/rollback-dialog";

describe("RollbackDialog", () => {
  it("asks for confirmation naming the version", () => {
    render(
      <RollbackDialog
        versionNumber={4}
        pending={false}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(
      screen.getByRole("heading", { name: "Roll back to v4?" })
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The board is re-flashed with v4; in virtual mode it is reloaded."
      )
    ).toBeInTheDocument();
  });

  it("confirms and cancels", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <RollbackDialog
        versionNumber={4}
        pending={false}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Roll back" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("disables the buttons while pending", () => {
    render(
      <RollbackDialog
        versionNumber={4}
        pending
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /Roll back/ })).toBeDisabled();
  });
});
