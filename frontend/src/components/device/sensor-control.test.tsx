import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SensorControl } from "@/components/device/sensor-control";
import type { HardwareChannel } from "@/services/hardware.service";

vi.mock("@/components/ui/slider", () => ({
  Slider: (props: {
    value: number[];
    onValueChange: (v: number[]) => void;
    onValueCommitted: (v: number[]) => void;
    disabled?: boolean;
  }) => (
    <div>
      <output data-testid="slider-value">{props.value[0]}</output>
      <button onClick={() => props.onValueChange([50])}>drag</button>
      <button onClick={() => props.onValueCommitted([50])}>release</button>
      <button onClick={() => props.onValueCommitted([7.4])}>
        release-fraction
      </button>
    </div>
  ),
}));

const temp = {
  id: "temp",
  kind: "sensor",
  type: "float",
  unit: "°C",
  range: [-40, 80],
  resolution: 0.1,
  neutral: 25,
  default_hysteresis: 0.5,
  injectable: true,
} as HardwareChannel;

describe("SensorControl", () => {
  it("shows the dragged value, sends nothing until release, then holds the committed value", () => {
    const onCommit = vi.fn();
    const { rerender } = render(
      <SensorControl channel={temp} value={20} onCommit={onCommit} />
    );
    expect(screen.getByText("20.0 °C")).toBeInTheDocument();

    fireEvent.click(screen.getByText("drag"));
    expect(screen.getByText("50.0 °C")).toBeInTheDocument();
    expect(onCommit).not.toHaveBeenCalled();

    // Telemetry arriving mid-drag does not move the control.
    rerender(<SensorControl channel={temp} value={21} onCommit={onCommit} />);
    expect(screen.getByText("50.0 °C")).toBeInTheDocument();

    fireEvent.click(screen.getByText("release"));
    expect(onCommit).toHaveBeenCalledWith(50);
    expect(screen.getByText("50.0 °C")).toBeInTheDocument();
  });

  describe("after a commit", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    const commitFifty = (value: number) => {
      const view = render(
        <SensorControl channel={temp} value={value} onCommit={vi.fn()} />
      );
      fireEvent.click(screen.getByText("drag"));
      fireEvent.click(screen.getByText("release"));
      return view;
    };

    it("keeps the committed value while old telemetry arrives", () => {
      const { rerender } = commitFifty(20);
      expect(screen.getByText("50.0 °C")).toBeInTheDocument();
      rerender(
        <SensorControl channel={temp} value={20.1} onCommit={vi.fn()} />
      );
      expect(screen.getByText("50.0 °C")).toBeInTheDocument();
    });

    it("follows telemetry once it matches the committed value", () => {
      const { rerender } = commitFifty(20);
      rerender(
        <SensorControl channel={temp} value={50.04} onCommit={vi.fn()} />
      );
      expect(screen.getByText("50.0 °C")).toBeInTheDocument();
      rerender(<SensorControl channel={temp} value={42} onCommit={vi.fn()} />);
      expect(screen.getByText("42.0 °C")).toBeInTheDocument();
    });

    it("reverts to telemetry after one second without a match", () => {
      vi.useFakeTimers();
      commitFifty(20);
      expect(screen.getByText("50.0 °C")).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.getByText("20.0 °C")).toBeInTheDocument();
    });

    it("holds a switch's committed state the same way", () => {
      vi.useFakeTimers();
      const motion = {
        ...temp,
        id: "motion",
        type: "bool",
        unit: null,
      } as HardwareChannel;
      const { rerender } = render(
        <SensorControl channel={motion} value={0} onCommit={vi.fn()} />
      );
      fireEvent.click(screen.getByRole("switch"));
      expect(screen.getByRole("switch")).toBeChecked();
      rerender(<SensorControl channel={motion} value={0} onCommit={vi.fn()} />);
      expect(screen.getByRole("switch")).toBeChecked();
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.getByRole("switch")).not.toBeChecked();
    });

    it("does not hold a stale timer after unmount", () => {
      vi.useFakeTimers();
      const { unmount } = commitFifty(20);
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    });
  });

  it("rounds an int channel's commit to an integer", () => {
    const onCommit = vi.fn();
    const light = {
      ...temp,
      id: "light",
      type: "int",
      resolution: 1,
      unit: null,
    } as HardwareChannel;
    render(<SensorControl channel={light} value={1} onCommit={onCommit} />);
    fireEvent.click(screen.getByText("release-fraction"));
    expect(onCommit).toHaveBeenCalledWith(7);
  });

  it("drops the drag when the control becomes disabled", () => {
    const { rerender } = render(
      <SensorControl channel={temp} value={20} onCommit={vi.fn()} />
    );
    fireEvent.click(screen.getByText("drag"));
    expect(screen.getByText("50.0 °C")).toBeInTheDocument();
    rerender(
      <SensorControl channel={temp} value={20} disabled onCommit={vi.fn()} />
    );
    expect(screen.getByText("20.0 °C")).toBeInTheDocument();
  });
});
