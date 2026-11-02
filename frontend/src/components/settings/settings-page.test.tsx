import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "@/components/settings/settings-page";
import { ApiError } from "@/lib/fetcher";
import { healthService, type Health } from "@/services/health.service";
import { settingsService, type Settings } from "@/services/settings.service";

const settings: Settings = {
  auto_approve: false,
  available_providers: ["gemini", "fake"],
  device_mode: "virtual",
  llm_fallback_provider: "fake",
  llm_model: "gemini-test",
  llm_provider: "gemini",
  max_attempts: 3,
  speak_results: false,
  voice_input_enabled: true,
};

const health = (warnings: string[] = []): Health => ({
  ok: true,
  lib_version: "1",
  proto: 1,
  services: { runner: "ok", builder: "ok", device: "ok", llm: "ok" },
  warnings,
});

const renderPage = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SettingsPage />
    </QueryClientProvider>
  );

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(healthService, "getHealth").mockResolvedValue(health());
    vi.spyOn(settingsService, "getSettings").mockResolvedValue(settings);
  });

  it("shows a skeleton, then the loaded values", async () => {
    renderPage();
    expect(
      screen.getByRole("status", { name: "Loading settings" })
    ).toBeVisible();
    expect(await screen.findByLabelText("Model")).toHaveValue("gemini-test");
    expect(screen.getByLabelText("Max attempts")).toHaveValue(3);
    expect(
      screen.getByRole("combobox", { name: "Provider" })
    ).toHaveTextContent("gemini");
    expect(
      screen.getByRole("combobox", { name: "Fallback provider" })
    ).toHaveTextContent("fake");
    expect(screen.getByRole("switch", { name: "Voice input" })).toBeChecked();
    expect(
      screen.getByRole("switch", { name: "Auto-approve" })
    ).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled();
  });

  it("offers only the available providers", async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole("combobox", { name: "Provider" })
    );
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["gemini", "fake"]);
  });

  it("labels the chat provider and shows the connect command only when it is selected", async () => {
    vi.spyOn(settingsService, "getSettings").mockResolvedValue({
      ...settings,
      available_providers: ["gemini", "chat"],
    });
    renderPage();
    const trigger = await screen.findByRole("combobox", { name: "Provider" });
    expect(screen.queryByText(/claude mcp add/)).not.toBeInTheDocument();
    await userEvent.click(trigger);
    await userEvent.click(
      await screen.findByRole("option", { name: "Chat app (MCP)" })
    );
    expect(screen.getByText(/claude mcp add/)).toBeVisible();
  });

  it("sends only the changed keys", async () => {
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue({ ...settings, llm_model: "m2", max_attempts: 5 });
    renderPage();
    const model = await screen.findByLabelText("Model");
    await userEvent.clear(model);
    await userEvent.type(model, "m2");
    const attempts = screen.getByLabelText("Max attempts");
    await userEvent.clear(attempts);
    await userEvent.type(attempts, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ llm_model: "m2", max_attempts: 5 })
    );
    expect(await screen.findByText("Saved")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("sends null when the fallback is set to None", async () => {
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue({ ...settings, llm_fallback_provider: null });
    renderPage();
    await userEvent.click(
      await screen.findByRole("combobox", { name: "Fallback provider" })
    );
    await userEvent.click(await screen.findByRole("option", { name: "None" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ llm_fallback_provider: null })
    );
  });

  it("sends a toggled switch", async () => {
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue({ ...settings, auto_approve: true });
    renderPage();
    await userEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ auto_approve: true })
    );
  });

  it("reset returns to the loaded values", async () => {
    renderPage();
    const model = await screen.findByLabelText("Model");
    await userEvent.type(model, "x");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(model).toHaveValue("gemini-test");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("blocks saving when max attempts is out of range", async () => {
    renderPage();
    const attempts = await screen.findByLabelText("Max attempts");
    await userEvent.clear(attempts);
    await userEvent.type(attempts, "9");
    expect(screen.getByText(/whole number from 1 to 8/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it.each([
    ["PROVIDER_UNCONFIGURED", "The anthropic provider has no API key."],
    ["RUN_ACTIVE", "A run is working; change the device mode afterwards."],
  ] as const)("shows a %s failure inline", async (code, message) => {
    vi.spyOn(settingsService, "updateSettings").mockRejectedValue(
      new ApiError(409, code, message)
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("shows the real-mode hint", async () => {
    renderPage();
    await screen.findByLabelText("Model");
    expect(
      screen.queryByText("Real mode needs the board; runs fail without it.")
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("combobox", { name: "Device mode" })
    );
    await userEvent.click(await screen.findByRole("option", { name: "Real" }));
    expect(
      screen.getByText("Real mode needs the board; runs fail without it.")
    ).toBeVisible();
  });

  it("shows health warnings above the form", async () => {
    vi.spyOn(healthService, "getHealth").mockResolvedValue(
      health(["Run budget is above the daily limit."])
    );
    renderPage();
    expect(
      await screen.findByText("Run budget is above the daily limit.")
    ).toBeVisible();
  });

  it("clears Saved on the next edit", async () => {
    vi.spyOn(settingsService, "updateSettings").mockResolvedValue({
      ...settings,
      auto_approve: true,
    });
    renderPage();
    const toggle = await screen.findByRole("switch", { name: "Auto-approve" });
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved")).toBeVisible();
    await userEvent.click(toggle);
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("keeps edits typed while the save is pending", async () => {
    let finish: (value: Settings) => void = () => {};
    vi.spyOn(settingsService, "updateSettings").mockReturnValue(
      new Promise<Settings>((resolve) => {
        finish = resolve;
      })
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const model = screen.getByLabelText("Model");
    await userEvent.type(model, "x");
    finish({ ...settings, auto_approve: true });
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Auto-approve" })).toBeChecked()
    );
    expect(model).toHaveValue("gemini-testx");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("sets the fallback to None when the provider becomes the fallback", async () => {
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue(settings);
    renderPage();
    await userEvent.click(
      await screen.findByRole("combobox", { name: "Provider" })
    );
    await userEvent.click(await screen.findByRole("option", { name: "fake" }));
    expect(
      screen.getByRole("combobox", { name: "Fallback provider" })
    ).toHaveTextContent("None");
    expect(screen.getByText(/Fallback set to None/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({
        llm_provider: "fake",
        llm_fallback_provider: null,
      })
    );
  });

  it("does not offer chat as a fallback provider", async () => {
    vi.spyOn(settingsService, "getSettings").mockResolvedValue({
      ...settings,
      available_providers: ["gemini", "fake", "chat"],
      llm_fallback_provider: null,
    });
    renderPage();
    await userEvent.click(
      await screen.findByRole("combobox", { name: "Fallback provider" })
    );
    expect(await screen.findByRole("option", { name: "fake" })).toBeVisible();
    expect(screen.queryByRole("option", { name: /chat/i })).toBeNull();
  });

  it("trims the model before sending and blocks a blank model", async () => {
    const update = vi
      .spyOn(settingsService, "updateSettings")
      .mockResolvedValue(settings);
    renderPage();
    const model = await screen.findByLabelText("Model");
    await userEvent.type(model, "  ");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(model);
    await userEvent.type(model, "   ");
    expect(screen.getByText("Enter a model name.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(model);
    await userEvent.type(model, " m3 ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ llm_model: "m3" })
    );
  });

  it("shows a current provider that is not in the available list", async () => {
    vi.mocked(settingsService.getSettings).mockResolvedValue({
      ...settings,
      llm_provider: "anthropic",
      llm_fallback_provider: null,
    });
    renderPage();
    const trigger = await screen.findByRole("combobox", { name: "Provider" });
    expect(trigger).toHaveTextContent("anthropic");
    await userEvent.click(trigger);
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "gemini",
      "fake",
      "anthropic",
    ]);
  });

  it("shows a load error and retries", async () => {
    const get = vi
      .spyOn(settingsService, "getSettings")
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"))
      .mockResolvedValue(settings);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Model")).toBeVisible();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
