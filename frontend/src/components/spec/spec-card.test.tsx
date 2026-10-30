import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpecCard } from "@/components/spec/spec-card";
import { ApiError } from "@/lib/fetcher";
import { hardwareService } from "@/services/hardware.service";
import * as decisions from "@/services/run-decisions.service";
import type { Run } from "@/services/runs.service";
import type { components } from "@/types/generated/api";

type Spec = components["schemas"]["Spec"];

const spec = (): Spec => ({
  summary: "Fan on when hot",
  rules: [
    {
      id: "r1",
      description: "Fan runs above 28 C",
      combine: "all",
      conditions: [{ channel: "temp", op: "gt", value: 28 }],
      then: { fan: true },
    },
  ],
  scenarios: [
    {
      id: "s1",
      title: "Hot room",
      given: { temp: 20 },
      steps: [
        {
          at_ms: 0,
          set: { temp: 30 },
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
    {
      id: "s2",
      title: "Cool room",
      given: {},
      steps: [{ at_ms: 0, set: { temp: 10 }, expect: [] }],
    },
  ],
});

const run = (over: Partial<Run> = {}): Run =>
  ({
    id: "r1",
    session_id: "s1",
    status: "awaiting_confirmation",
    question: null,
    proposed_spec: spec(),
    confirmed_spec: null,
    boundaries: ["Fan stays on at exactly 28 C"],
    spec_diff: {
      summary_changed: true,
      rules: { added: [], modified: ["r1"], removed: ["r2"], unchanged: [] },
      scenarios: {
        added: ["s1"],
        modified: [],
        removed: ["s3"],
        unchanged: ["s2"],
      },
    },
    ...over,
  }) as Run;

const renderCard = (r: Run) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrap = (x: Run) => (
    <QueryClientProvider client={queryClient}>
      <SpecCard run={x} />
    </QueryClientProvider>
  );
  const view = render(wrap(r));
  return { ...view, rerunWith: (x: Run) => view.rerender(wrap(x)) };
};

const openEditor = async () => {
  const button = screen.getByRole("button", { name: "Edit scenarios" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
};

describe("SpecCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue({
      components: [],
      channels: [
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
      ],
    } as never);
  });

  it("renders nothing without a spec or question", () => {
    const { container } = renderCard(
      run({ status: "failed", proposed_spec: null })
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("posts an answer and clears the box", async () => {
    const spy = vi.spyOn(decisions, "answerRun").mockResolvedValue(run());
    renderCard(
      run({
        status: "awaiting_answer",
        proposed_spec: null,
        question: { question: "Which pin?", reason: "Two fans exist" },
      })
    );
    expect(screen.getByText("Which pin?")).toBeVisible();
    expect(screen.getByText("Two fans exist")).toBeVisible();
    const send = screen.getByRole("button", { name: "Send answer" });
    expect(send).toBeDisabled();
    const box = screen.getByLabelText("Your answer");
    await userEvent.type(box, "the left one");
    await userEvent.click(send);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", "the left one"));
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("shows summary, boundaries, badges and the removed line", () => {
    renderCard(run());
    expect(screen.getByText("Fan on when hot")).toBeVisible();
    expect(screen.getByText("Fan stays on at exactly 28 C")).toBeVisible();
    expect(screen.getAllByText("Changed")).toHaveLength(2); // summary + rule r1
    expect(screen.getAllByText("New")).toHaveLength(1);
    expect(screen.getByText("Removed: s3, r2")).toBeVisible();
  });

  it("confirms without edits by sending an empty body", async () => {
    const spy = vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", undefined));
  });

  it("sends the edited spec in ms", async () => {
    const spy = vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    renderCard(run());
    await openEditor();
    const time = await screen.findByLabelText(
      "Scenario s1 step 1 time (seconds)"
    );
    await userEvent.clear(time);
    await userEvent.type(time, "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const sent = spy.mock.calls[0]?.[1];
    expect(sent?.scenarios[0]?.steps[0]?.at_ms).toBe(2500);
    expect(sent?.rules).toEqual(spec().rules);
  });

  it("blocks Confirm on client validation errors", async () => {
    const spy = vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    renderCard(run());
    await openEditor();
    await userEvent.click(
      (await screen.findAllByRole("button", { name: /Add step/ }))[0]!
    );
    const time = screen.getByLabelText("Scenario s1 step 2 time (seconds)");
    await userEvent.clear(time);
    await userEvent.type(time, "0");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    expect(
      screen.getAllByText(/later than the previous step/).length
    ).toBeGreaterThan(0);
    expect(spy).not.toHaveBeenCalled();
  });

  it("blocks Confirm for an out of range value", async () => {
    renderCard(run());
    await openEditor();
    const temp = await screen.findByLabelText("Scenario s1 step 1 temp value");
    await userEvent.clear(temp);
    await userEvent.type(temp, "99");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    expect(
      screen.getAllByText(/temp must be between -10 and 50/).length
    ).toBeGreaterThan(0);
  });

  it("lists SPEC_INVALID errors", async () => {
    vi.spyOn(decisions, "confirmRun").mockRejectedValue(
      new ApiError(422, "SPEC_INVALID", "invalid", {
        errors: [
          {
            code: "out_of_range",
            path: "scenarios[0].steps[0]",
            message: "too hot",
          },
        ],
      })
    );
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(
      await screen.findByText("Scenario s1 › step 1: too hot")
    ).toBeVisible();
  });

  it("requires feedback to reject and posts it", async () => {
    const spy = vi.spyOn(decisions, "rejectRun").mockResolvedValue(run());
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const send = screen.getByRole("button", { name: "Send feedback" });
    expect(send).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("What should change?"),
      "too slow"
    );
    await userEvent.click(send);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", "too slow"));
  });

  it("keeps showing edits after Done editing", async () => {
    renderCard(run());
    await openEditor();
    const title = await screen.findByLabelText("Scenario s1 title");
    await userEvent.clear(title);
    await userEvent.type(title, "Scorching room");
    await userEvent.click(screen.getByRole("button", { name: "Done editing" }));
    expect(screen.getByText("Scorching room")).toBeVisible();
    expect(screen.queryByText("Hot room")).toBeNull();
    // The local edit is marked without hiding what the proposal changed.
    expect(screen.getByText("New")).toBeVisible();
    expect(screen.getByText("Edited")).toBeVisible();
  });

  it("sends an empty body after an edit is reverted", async () => {
    const spy = vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    renderCard(run());
    await openEditor();
    const title = await screen.findByLabelText("Scenario s1 title");
    await userEvent.type(title, "X");
    await userEvent.type(title, "{Backspace}");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", undefined));
  });

  it("drops edits when another run is shown", async () => {
    const spy = vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    const { rerunWith } = renderCard(run());
    await openEditor();
    const title = await screen.findByLabelText("Scenario s1 title");
    await userEvent.type(title, "X");
    rerunWith(run({ id: "r2" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r2", undefined));
  });

  it("disables editing while hardware loads and after it fails", async () => {
    vi.spyOn(hardwareService, "getHardware").mockRejectedValue(
      new Error("down")
    );
    renderCard(run());
    expect(
      screen.getByRole("button", { name: "Edit scenarios" })
    ).toBeDisabled();
    expect(await screen.findByText(/could not be loaded/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Edit scenarios" })
    ).toBeDisabled();
  });

  it("clears a stale confirm error when the spec is edited", async () => {
    vi.spyOn(decisions, "confirmRun").mockRejectedValue(
      new ApiError(422, "SPEC_INVALID", "invalid", { errors: ["bad"] })
    );
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("bad")).toBeVisible();
    await openEditor();
    await userEvent.type(
      await screen.findByLabelText("Scenario s1 title"),
      "X"
    );
    expect(screen.queryByText("bad")).toBeNull();
  });

  it("is read-only for a confirmed spec on a finished run", () => {
    renderCard(
      run({ status: "succeeded", proposed_spec: null, confirmed_spec: spec() })
    );
    expect(screen.getByText("Fan on when hot")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("New")).toBeNull();
  });
});
