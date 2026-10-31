import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DevicePanel } from "@/components/device/device-panel";
import { ApiError } from "@/lib/fetcher";
import { deviceCommandService } from "@/services/device-command.service";
import { hardwareService, type Hardware } from "@/services/hardware.service";
import type { DeviceTelemetryEvent } from "@/types/generated/events";

let currentTelemetry: DeviceTelemetryEvent | null = null;

vi.mock("@/hooks/use-telemetry", () => ({
  useTelemetry: () => currentTelemetry,
}));

const channel = (
  overrides: Partial<Hardware["channels"][number]>
): Hardware["channels"][number] => ({
  id: "x",
  kind: "sensor",
  type: "float",
  unit: null,
  range: null,
  resolution: null,
  neutral: null,
  default_hysteresis: null,
  injectable: true,
  ...overrides,
});

const hardware: Hardware = {
  components: [],
  channels: [
    channel({
      id: "temp",
      unit: "°C",
      range: [-40, 80],
      resolution: 0.1,
      neutral: 25,
    }),
    channel({
      id: "light",
      type: "int",
      range: [0, 4095],
      resolution: 1,
      neutral: 2000,
    }),
    channel({ id: "motion", type: "bool", neutral: false }),
    channel({ id: "fan", kind: "actuator", type: "bool", neutral: false }),
    channel({
      id: "display",
      kind: "display",
      type: "text",
      rows: 4,
      cols: 21,
    }),
  ],
} as Hardware;

const telemetry = (
  overrides: Partial<DeviceTelemetryEvent> = {}
): DeviceTelemetryEvent => ({
  type: "device.telemetry",
  ts: "2026-01-01T00:00:00Z",
  mode: "run",
  t: 1,
  s: { temp: 31.5, light: 100, motion: 1 },
  a: { fan: 1 },
  d: ["Hello", "", "", ""],
  ...overrides,
});

const getSlider = (name: string): HTMLInputElement => {
  const input = screen
    .getAllByLabelText(name)
    .find((el): el is HTMLInputElement => el instanceof HTMLInputElement);
  if (!input) throw new Error(`no slider named ${name}`);
  return input;
};

const findSlider = (name: string) => waitFor(() => getSlider(name));

function renderPanel(mode: "real" | "virtual" = "virtual") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = (m: "real" | "virtual") => (
    <QueryClientProvider client={client}>
      <DevicePanel mode={m} />
    </QueryClientProvider>
  );
  const result = render(view(mode));
  return { ...result, rerenderPanel: () => result.rerender(view(mode)) };
}

describe("DevicePanel", () => {
  beforeEach(() => {
    currentTelemetry = null;
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue(hardware);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a skeleton while hardware loads", () => {
    vi.spyOn(hardwareService, "getHardware").mockReturnValue(
      new Promise(() => {})
    );
    renderPanel();
    expect(document.querySelector('[aria-busy="true"]')).toBeInTheDocument();
  });

  it("shows the error with Retry", async () => {
    const get = vi
      .spyOn(hardwareService, "getHardware")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(hardware);
    renderPanel();
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await findSlider("temp")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("builds a control per sensor channel with the channel's bounds", async () => {
    renderPanel();
    const temp = await findSlider("temp");
    expect(temp).toHaveAttribute("min", "-40");
    expect(temp).toHaveAttribute("max", "80");
    expect(temp).toHaveAttribute("step", "0.1");
    expect(temp).toHaveAttribute("aria-valuetext", "25.0 °C");
    const light = getSlider("light");
    expect(light).toHaveAttribute("max", "4095");
    expect(screen.getByRole("switch", { name: "motion" })).toBeInTheDocument();
  });

  it("shows neutral values and a waiting message before telemetry", async () => {
    renderPanel();
    expect(await screen.findByText("25.0 °C")).toBeInTheDocument();
    expect(screen.getByText("2000")).toBeInTheDocument();
    expect(screen.getByText("Waiting for telemetry…")).toBeInTheDocument();
    expect(screen.getByLabelText("fan off")).toBeInTheDocument();
  });

  it("follows telemetry for values, indicators and display lines", async () => {
    currentTelemetry = telemetry();
    renderPanel();
    expect(await screen.findByText("31.5 °C")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "motion" })).toBeChecked();
    expect(screen.getByLabelText("fan on")).toBeInTheDocument();
    expect(
      screen.queryByText("Waiting for telemetry…")
    ).not.toBeInTheDocument();
    const lines = screen.getAllByTestId("oled-line");
    expect(lines).toHaveLength(4);
    expect(lines[0]?.textContent).toBe("Hello".padEnd(21, " "));
    expect(screen.getByLabelText("Display")).toBeInTheDocument();
  });

  it("clips long display lines to the column count", async () => {
    currentTelemetry = telemetry({ d: ["x".repeat(40), "", "", "", "extra"] });
    renderPanel();
    const lines = await screen.findAllByTestId("oled-line");
    expect(lines).toHaveLength(4);
    expect(lines[0]?.textContent).toBe("x".repeat(21));
  });

  it("sends set_sensor when a slider value is committed", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockResolvedValue({ ok: true });
    currentTelemetry = telemetry();
    renderPanel();
    const temp = await findSlider("temp");
    fireEvent.keyDown(temp, { key: "ArrowRight" });
    await waitFor(() => expect(setSensor).toHaveBeenCalledTimes(1));
    const [channelId, value] = setSensor.mock.calls[0] ?? [];
    expect(channelId).toBe("temp");
    expect(value).toBeCloseTo(31.6);
  });

  it("sends set_sensor when a switch is toggled", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockResolvedValue({ ok: true });
    renderPanel();
    fireEvent.click(await screen.findByRole("switch", { name: "motion" }));
    await waitFor(() => expect(setSensor).toHaveBeenCalledWith("motion", true));
  });

  it("shows read-only values with no controls in real mode", async () => {
    currentTelemetry = telemetry();
    renderPanel("real");
    expect(await screen.findByText("31.5 °C")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.getByLabelText("fan on")).toBeInTheDocument();
  });

  it("disables the controls while a test runs", async () => {
    currentTelemetry = telemetry({ mode: "test" });
    renderPanel();
    expect(await screen.findByText("Test running")).toBeInTheDocument();
    expect(getSlider("temp")).toBeDisabled();
    expect(screen.getByRole("switch", { name: "motion" })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });

  it("says when an override is active and keeps the controls enabled", async () => {
    currentTelemetry = telemetry({ mode: "override" });
    renderPanel();
    expect(await screen.findByText("Override active")).toBeInTheDocument();
    expect(screen.queryByText("Test running")).not.toBeInTheDocument();
    expect(getSlider("temp")).not.toBeDisabled();
    expect(screen.getByRole("switch", { name: "motion" })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });

  it("shows a command error until a later command succeeds", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockRejectedValueOnce(new ApiError(409, "RUN_ACTIVE", "A run is active"))
      .mockResolvedValueOnce({ ok: true });
    renderPanel();
    const toggle = await screen.findByRole("switch", { name: "motion" });
    fireEvent.click(toggle);
    expect(await screen.findByText("A run is active")).toBeInTheDocument();
    fireEvent.click(toggle);
    await waitFor(() => expect(setSensor).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText("A run is active")).not.toBeInTheDocument()
    );
  });

  it("keeps the command error while a later command is pending", async () => {
    let resolveSecond: (v: { ok: boolean }) => void = () => {};
    vi.spyOn(deviceCommandService, "setSensor")
      .mockRejectedValueOnce(new ApiError(409, "RUN_ACTIVE", "A run is active"))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecond = resolve;
        })
      );
    renderPanel();
    const toggle = await screen.findByRole("switch", { name: "motion" });
    fireEvent.click(toggle);
    expect(await screen.findByText("A run is active")).toBeInTheDocument();
    fireEvent.click(toggle);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText("A run is active")).toBeInTheDocument();
    resolveSecond({ ok: true });
    await waitFor(() =>
      expect(screen.queryByText("A run is active")).not.toBeInTheDocument()
    );
  });

  it("shows a Virtual or Real badge from the mode", async () => {
    const { unmount } = renderPanel("virtual");
    await findSlider("temp");
    expect(screen.getByText("Virtual")).toBeInTheDocument();
    unmount();
    renderPanel("real");
    expect(await screen.findByText("Real")).toBeInTheDocument();
  });

  it("shows an empty 4 by 21 display box before telemetry", async () => {
    renderPanel();
    const lines = await screen.findAllByTestId("oled-line");
    expect(lines).toHaveLength(4);
    for (const line of lines) expect(line.textContent).toBe(" ".repeat(21));
  });

  it("shows an actuator as off when telemetry reports 0", async () => {
    currentTelemetry = telemetry({ a: { fan: 0 } });
    renderPanel();
    expect(await screen.findByLabelText("fan off")).toBeInTheDocument();
    expect(screen.queryByLabelText("fan on")).not.toBeInTheDocument();
  });

  it("sends nothing for a switch toggle while a test runs", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockResolvedValue({ ok: true });
    currentTelemetry = telemetry({ mode: "test" });
    renderPanel();
    expect(await screen.findByText("Test running")).toBeInTheDocument();
    expect(getSlider("temp")).toBeDisabled();
    fireEvent.click(screen.getByRole("switch", { name: "motion" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(setSensor).not.toHaveBeenCalled();
  });

  it("sends nothing for a slider keyboard change while a test runs", async () => {
    const setSensor = vi
      .spyOn(deviceCommandService, "setSensor")
      .mockResolvedValue({ ok: true });
    currentTelemetry = telemetry({ mode: "test" });
    renderPanel();
    expect(await screen.findByText("Test running")).toBeInTheDocument();
    fireEvent.keyDown(getSlider("temp"), { key: "ArrowRight" });
    await new Promise((r) => setTimeout(r, 20));
    expect(setSensor).not.toHaveBeenCalled();
  });

  it("makes every sensor controllable in virtual mode, including bool ones", async () => {
    renderPanel();
    await findSlider("temp");
    expect(getSlider("light")).not.toBeDisabled();
    expect(screen.getByRole("switch", { name: "motion" })).toBeInTheDocument();
  });
});
