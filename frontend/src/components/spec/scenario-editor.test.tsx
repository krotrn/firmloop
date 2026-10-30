import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ScenarioEditor } from "@/components/spec/scenario-editor";
import type { Scenario } from "@/lib/spec-edit";
import type { HardwareChannel } from "@/services/hardware.service";

const channels: HardwareChannel[] = [
  {
    id: "temp",
    kind: "sensor",
    type: "float",
    unit: "C",
    range: [-10, 50],
    resolution: 0.1,
    neutral: 20,
    default_hysteresis: null,
    injectable: true,
  },
  {
    id: "door",
    kind: "sensor",
    type: "bool",
    unit: null,
    range: null,
    resolution: null,
    neutral: false,
    default_hysteresis: null,
    injectable: true,
  },
  {
    id: "fan",
    kind: "actuator",
    type: "bool",
    unit: null,
    range: null,
    resolution: null,
    neutral: false,
    default_hysteresis: null,
    injectable: false,
  },
  {
    id: "display",
    kind: "display",
    type: "text",
    unit: null,
    range: null,
    resolution: null,
    neutral: null,
    default_hysteresis: null,
    injectable: false,
    rows: 4,
    cols: 21,
  },
];

const base = (): Scenario[] => [
  {
    id: "s1",
    title: "Hot room",
    given: { temp: 20 },
    steps: [
      {
        at_ms: 0,
        set: { temp: 30, door: true },
        expect: [
          {
            channel: "fan",
            op: "eq",
            value: true,
            hold_ms: 0,
            tolerance: 0,
            within_ms: 0,
          },
        ],
      },
    ],
  },
];

function Harness({ onChange }: { onChange: (v: Scenario[]) => void }) {
  const [value, setValue] = useState(base());
  return (
    <ScenarioEditor
      value={value}
      channels={channels}
      onChange={(v) => {
        setValue(v);
        onChange(v);
      }}
    />
  );
}

describe("ScenarioEditor", () => {
  it("edits a step time in seconds and stores ms", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const time = screen.getByLabelText("Scenario s1 step 1 time (seconds)");
    await userEvent.clear(time);
    await userEvent.type(time, "1.5");
    expect(onChange.mock.lastCall?.[0][0].steps[0].at_ms).toBe(1500);
  });

  it("uses a switch for bool values and a number input with range for floats", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const sw = screen.getByRole("switch", {
      name: "Scenario s1 step 1 door value",
    });
    expect(sw).toBeChecked();
    await userEvent.click(sw);
    expect(onChange.mock.lastCall?.[0][0].steps[0].set.door).toBe(false);
    const temp = screen.getByLabelText("Scenario s1 step 1 temp value");
    expect(temp).toHaveAttribute("min", "-10");
    expect(temp).toHaveAttribute("max", "50");
    expect(temp).toHaveAttribute("step", "0.1");
  });

  it("adds and deletes steps", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Add step/ }));
    expect(onChange.mock.lastCall?.[0][0].steps).toHaveLength(2);
    expect(onChange.mock.lastCall?.[0][0].steps[1].at_ms).toBe(1000);
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Scenario s1 step 2" })
    );
    expect(onChange.mock.lastCall?.[0][0].steps).toHaveLength(1);
  });

  it("adds and deletes expectations", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Add expectation/ })
    );
    expect(onChange.mock.lastCall?.[0][0].steps[0].expect).toHaveLength(2);
    await userEvent.click(
      screen.getByRole("button", {
        name: "Delete Scenario s1 step 1 expectation 2",
      })
    );
    expect(onChange.mock.lastCall?.[0][0].steps[0].expect).toHaveLength(1);
  });

  it("offers display operators only for the display channel", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const section = screen.getByRole("region", { name: "Scenario s1" });
    // actuator: a single fixed operator, no operator select
    expect(
      within(section).queryByRole("combobox", {
        name: "Scenario s1 step 1 expectation 1 operator",
      })
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("combobox", {
        name: "Scenario s1 step 1 expectation 1 channel",
      })
    );
    await userEvent.click(
      await screen.findByRole("option", { name: "display" })
    );
    expect(onChange.mock.lastCall?.[0][0].steps[0].expect[0].op).toBe(
      "display_contains"
    );
    await userEvent.click(
      screen.getByRole("combobox", {
        name: "Scenario s1 step 1 expectation 1 operator",
      })
    );
    expect(
      await screen
        .findAllByRole("option")
        .then((o) => o.map((x) => x.textContent))
    ).toEqual(["contains", "row shows", "contains value of"]);
  });

  it("shows validation messages for non-increasing times", async () => {
    render(<Harness onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Add step/ }));
    const time = screen.getByLabelText("Scenario s1 step 2 time (seconds)");
    await userEvent.clear(time);
    await userEvent.type(time, "0");
    expect(
      screen.getByText("The time must be later than the previous step.")
    ).toBeVisible();
  });

  it("keeps within and hold when the channel changes, and stores them in ms", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const within = screen.getByLabelText(
      "Scenario s1 step 1 expectation 1 within (s)"
    );
    await userEvent.clear(within);
    await userEvent.type(within, "1.15");
    const hold = screen.getByLabelText(
      "Scenario s1 step 1 expectation 1 hold (s)"
    );
    await userEvent.clear(hold);
    await userEvent.type(hold, "0.1");
    const e = () => onChange.mock.lastCall?.[0][0].steps[0].expect[0];
    expect(e().within_ms).toBe(1150);
    expect(e().hold_ms).toBe(100);
    await userEvent.click(
      screen.getByRole("combobox", {
        name: "Scenario s1 step 1 expectation 1 channel",
      })
    );
    await userEvent.click(
      await screen.findByRole("option", { name: "display" })
    );
    expect(e().channel).toBe("display");
    expect(e().within_ms).toBe(1150);
    expect(e().hold_ms).toBe(100);
  });

  it("clears fields the new operator does not use", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const channel = screen.getByRole("combobox", {
      name: "Scenario s1 step 1 expectation 1 channel",
    });
    await userEvent.click(channel);
    await userEvent.click(
      await screen.findByRole("option", { name: "display" })
    );
    const op = () =>
      screen.getByRole("combobox", {
        name: "Scenario s1 step 1 expectation 1 operator",
      });
    await userEvent.click(op());
    await userEvent.click(
      await screen.findByRole("option", { name: "row shows" })
    );
    expect(onChange.mock.lastCall?.[0][0].steps[0].expect[0].row).toBe(0);
    await userEvent.click(op());
    await userEvent.click(
      await screen.findByRole("option", { name: "contains value of" })
    );
    let e = onChange.mock.lastCall?.[0][0].steps[0].expect[0];
    expect(e.row).toBeNull();
    expect(e.source_channel).toBe("temp");
    const tol = screen.getByLabelText(
      "Scenario s1 step 1 expectation 1 tolerance"
    );
    await userEvent.clear(tol);
    await userEvent.type(tol, "2");
    expect(onChange.mock.lastCall?.[0][0].steps[0].expect[0].tolerance).toBe(2);
    await userEvent.click(op());
    await userEvent.click(
      await screen.findByRole("option", { name: "contains" })
    );
    e = onChange.mock.lastCall?.[0][0].steps[0].expect[0];
    expect(e.tolerance).toBe(0);
    expect(e.source_channel).toBeNull();
    expect(e.row).toBeNull();
  });
});
