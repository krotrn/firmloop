import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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

  it("shows rules read-only with their description and diff badge", () => {
    renderCard(run());
    const description = screen.getByText("Fan runs above 28 C");
    expect(description).toBeVisible();
    expect(
      within(description.parentElement!).getByText("Changed")
    ).toBeVisible();
    expect(screen.queryByLabelText(/Fan runs above/)).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows no removed line and no boundaries heading when there are none", () => {
    renderCard(
      run({
        boundaries: [],
        spec_diff: {
          summary_changed: false,
          rules: { added: [], modified: [], removed: [], unchanged: ["r1"] },
          scenarios: {
            added: [],
            modified: [],
            removed: [],
            unchanged: ["s1", "s2"],
          },
        },
      })
    );
    expect(screen.queryByText(/^Removed:/)).toBeNull();
    expect(screen.queryByText("Boundaries")).toBeNull();
    expect(screen.queryByText("New")).toBeNull();
    expect(screen.queryByText("Changed")).toBeNull();
  });

  it("marks only changed scenarios with a badge", () => {
    renderCard(
      run({
        spec_diff: {
          summary_changed: false,
          rules: { added: [], modified: [], removed: [], unchanged: ["r1"] },
          scenarios: {
            added: [],
            modified: ["s2"],
            removed: [],
            unchanged: ["s1"],
          },
        },
      })
    );
    expect(screen.getAllByText("Changed")).toHaveLength(1);
    expect(screen.queryByText("New")).toBeNull();
  });

  it("summarises each scenario step with its set values and expectations", () => {
    renderCard(run());
    expect(screen.getByText("Hot room")).toBeVisible();
    expect(
      screen.getByText("At 0 s set temp = 30 expect fan is true")
    ).toBeVisible();
    expect(screen.getByText("At 0 s set temp = 10")).toBeVisible();
  });

  it("words a display_contains expectation like the Tests tab", () => {
    const withDisplay = spec();
    withDisplay.scenarios[0]!.steps[0]!.expect = [
      {
        channel: "display",
        op: "display_contains",
        value: "HOT",
        row: 1,
        hold_ms: 0,
        tolerance: 0,
        within_ms: 0,
      },
    ];
    renderCard(run({ proposed_spec: withDisplay }));
    expect(
      screen.getByText("At 0 s set temp = 30 expect display row 1 contains HOT")
    ).toBeVisible();
  });

  it("keeps Send answer disabled for whitespace and trims the answer", async () => {
    const spy = vi.spyOn(decisions, "answerRun").mockResolvedValue(run());
    renderCard(
      run({
        status: "awaiting_answer",
        proposed_spec: null,
        question: { question: "Which pin?", reason: "" },
      })
    );
    const send = screen.getByRole("button", { name: "Send answer" });
    const box = screen.getByLabelText("Your answer");
    await userEvent.type(box, "   ");
    expect(send).toBeDisabled();
    await userEvent.type(box, " left  ");
    await userEvent.click(send);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", "left"));
  });

  it("limits the answer and the feedback to 2000 characters", async () => {
    const { unmount } = renderCard(
      run({
        status: "awaiting_answer",
        proposed_spec: null,
        question: { question: "Which pin?", reason: "" },
      })
    );
    expect(screen.getByLabelText("Your answer")).toHaveAttribute(
      "maxlength",
      "2000"
    );
    unmount();
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    expect(screen.getByLabelText("What should change?")).toHaveAttribute(
      "maxlength",
      "2000"
    );
  });

  it("shows an answer error and keeps the typed answer", async () => {
    vi.spyOn(decisions, "answerRun").mockRejectedValue(new Error("offline"));
    renderCard(
      run({
        status: "awaiting_answer",
        proposed_spec: null,
        question: { question: "Which pin?", reason: "" },
      })
    );
    const box = screen.getByLabelText("Your answer");
    await userEvent.type(box, "left");
    await userEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(box).toHaveValue("left");
  });

  it("trims reject feedback, keeps it disabled for whitespace", async () => {
    const spy = vi.spyOn(decisions, "rejectRun").mockResolvedValue(run());
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const box = screen.getByLabelText("What should change?");
    await userEvent.type(box, "   ");
    expect(
      screen.getByRole("button", { name: "Send feedback" })
    ).toBeDisabled();
    await userEvent.type(box, " too slow ");
    await userEvent.click(
      screen.getByRole("button", { name: "Send feedback" })
    );
    await waitFor(() => expect(spy).toHaveBeenCalledWith("r1", "too slow"));
  });

  it("closes the reject form with Cancel", async () => {
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("What should change?")).toBeNull();
  });

  it("shows a reject error", async () => {
    vi.spyOn(decisions, "rejectRun").mockRejectedValue(new Error("nope"));
    renderCard(run());
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    await userEvent.type(screen.getByLabelText("What should change?"), "x");
    await userEvent.click(
      screen.getByRole("button", { name: "Send feedback" })
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("refreshes the session queries after confirm", async () => {
    vi.spyOn(decisions, "confirmRun").mockResolvedValue(run());
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <SpecCard run={run()} />
      </QueryClientProvider>
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] })
    );
  });

  it("shows the confirmed spec read-only for a failed run too", () => {
    renderCard(
      run({ status: "failed", proposed_spec: null, confirmed_spec: spec() })
    );
    expect(screen.getByText("Confirmed spec")).toBeVisible();
    expect(screen.getByText("Hot room")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
