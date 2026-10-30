import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HardwarePage } from "@/components/hardware/hardware-page";
import { ApiError } from "@/lib/fetcher";
import { hardwareService, type Hardware } from "@/services/hardware.service";

const hardware: Hardware = {
  components: [
    {
      id: "dht22",
      name: "DHT22",
      kind: "sensor",
      pins: { data: 4 },
      channels: ["temp", "humidity"],
      ai_notes: "Reads at most every two seconds.",
    },
  ],
  channels: [
    {
      id: "temp",
      kind: "sensor",
      type: "float",
      unit: "°C",
      range: [-40, 80],
      resolution: 0.1,
      neutral: null,
      default_hysteresis: 0.5,
      injectable: true,
    },
    {
      id: "display",
      kind: "display",
      type: "text",
      unit: null,
      range: null,
      resolution: null,
      neutral: "",
      default_hysteresis: null,
      injectable: false,
      rows: 2,
      cols: 16,
    },
  ],
};

const renderPage = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <HardwarePage />
    </QueryClientProvider>
  );

describe("HardwarePage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a skeleton while loading", () => {
    vi.spyOn(hardwareService, "getHardware").mockReturnValue(
      new Promise(() => {})
    );
    renderPage();
    expect(
      screen.getByRole("status", { name: "Loading hardware" })
    ).toBeVisible();
  });

  it("shows components with pins, channels and notes", async () => {
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue(hardware);
    renderPage();
    expect(await screen.findByText("DHT22")).toBeVisible();
    expect(screen.getByText("data → GPIO 4")).toBeVisible();
    expect(screen.getByText("temp, humidity")).toBeVisible();
    expect(screen.getByText("Reads at most every two seconds.")).toBeVisible();
  });

  it("shows bulleted notes as a list with channel names as code", async () => {
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue({
      ...hardware,
      components: [
        {
          ...hardware.components[0]!,
          ai_notes:
            "- `temp` (float): degrees Celsius.\n- Updates every 2 s.\n",
        },
      ],
    });
    renderPage();
    const notes = await screen.findByRole("list", { name: "DHT22 notes" });
    const items = within(notes).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "temp (float): degrees Celsius.",
      "Updates every 2 s.",
    ]);
    expect(within(items[0]!).getByText("temp").tagName).toBe("CODE");
  });

  it("shows a row per channel with range and resolution", async () => {
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue(hardware);
    renderPage();
    const table = await screen.findByRole("table", { name: "Channels" });
    const temp = within(table).getByRole("row", { name: /^temp/ });
    expect(within(temp).getByText("-40 to 80")).toBeVisible();
    expect(within(temp).getByText("0.1")).toBeVisible();
    expect(within(temp).getByText("°C")).toBeVisible();
    expect(within(temp).getByText("Yes")).toBeVisible();
    expect(screen.getByRole("region", { name: "Channels" })).toHaveAttribute(
      "tabindex",
      "0"
    );
    const display = within(table).getByRole("row", { name: /^display/ });
    expect(within(display).getByText("16 × 2")).toBeVisible();
    // unit, range, resolution, neutral (empty string) and hysteresis are all missing.
    expect(within(display).getAllByText("—")).toHaveLength(5);
  });

  it("shows the error and retries", async () => {
    const get = vi
      .spyOn(hardwareService, "getHardware")
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"))
      .mockResolvedValue(hardware);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("DHT22")).toBeVisible();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
