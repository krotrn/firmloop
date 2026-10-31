import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeTab } from "@/components/code/code-tab";
import { ApiError } from "@/lib/fetcher";
import { CancelWaitingProvider } from "@/providers/cancel-waiting-provider";
import * as attemptsService from "@/services/attempts.service";
import { hardwareService } from "@/services/hardware.service";
import { sessionsService } from "@/services/sessions.service";
import * as actions from "@/services/run-actions.service";
import type { Run } from "@/services/runs.service";
import * as versionsService from "@/services/versions.service";
import type { components } from "@/types/generated/api";

vi.mock("monaco-editor/editor/editor.api", () => ({}));
vi.mock("monaco-editor/languages/definitions/cpp/register.js", () => ({}));
vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  default: (props: {
    value?: string;
    theme?: string;
    onChange?: (value: string | undefined) => void;
    options?: { readOnly?: boolean; ariaLabel?: string };
  }) => (
    <textarea
      aria-label={props.options?.ariaLabel}
      readOnly={props.options?.readOnly}
      data-theme={props.theme}
      value={props.value ?? ""}
      onChange={(event) => props.onChange?.(event.target.value)}
    />
  ),
  DiffEditor: (props: {
    original?: string;
    modified?: string;
    options?: { renderSideBySide?: boolean };
  }) => (
    <div
      data-testid="diff"
      data-original={props.original}
      data-modified={props.modified}
      data-side-by-side={String(props.options?.renderSideBySide)}
    />
  ),
}));

type Spec = components["schemas"]["Spec"];
type Attempt = attemptsService.Attempt;

const spec = (): Spec => ({
  summary: "Fan on when hot",
  rules: [],
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
  ],
});

const attempt = (number: number, over: Partial<Attempt> = {}): Attempt => ({
  id: `a${number}`,
  number,
  source: "llm",
  stage_reached: "native_testing",
  code: `// attempt ${number}`,
  compile_ok: true,
  compile_log: null,
  safety_ok: true,
  safety_violations: [],
  test_results: [],
  tests_passed: 0,
  tests_total: 3,
  duration_ms: 100,
  feedback_sent: null,
  ...over,
});

const run = (over: Partial<Run> = {}): Run =>
  ({
    id: "r1",
    session_id: "s1",
    status: "failed",
    kind: "request",
    confirmed_spec: spec(),
    base_version: null,
    best_attempt_id: null,
    attempts: [],
    ...over,
  }) as unknown as Run;

const channels = [
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
] as const;

const renderTab = (r: Run) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <CancelWaitingProvider>
        <CodeTab run={r} sessionId="s1" />
      </CancelWaitingProvider>
    </QueryClientProvider>
  );
  return { invalidate };
};

const waiting = () =>
  new ApiError(409, "RUN_WAITING", "A run is waiting", { session_id: "s2" });

const created = { id: "new" } as Run;

describe("CodeTab", () => {
  beforeEach(() => {
    vi.spyOn(sessionsService, "getSession").mockResolvedValue({
      title: "Other session",
    } as Awaited<ReturnType<typeof sessionsService.getSession>>);
    vi.spyOn(hardwareService, "getHardware").mockResolvedValue({
      components: [],
      channels: [...channels],
    } as unknown as Awaited<ReturnType<typeof hardwareService.getHardware>>);
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue([
      attempt(1),
      attempt(2),
      attempt(3),
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.classList.remove("dark");
  });

  it("shows an empty state when there are no attempts", async () => {
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue([]);
    renderTab(run());
    expect(await screen.findByText(/No code yet/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("selects the best attempt by default", async () => {
    renderTab(run({ best_attempt_id: "a2" }));
    expect(await screen.findByLabelText("Attempt code")).toHaveValue(
      "// attempt 2"
    );
  });

  it("falls back to the latest attempt without a best one", async () => {
    renderTab(run());
    expect(await screen.findByLabelText("Attempt code")).toHaveValue(
      "// attempt 3"
    );
  });

  it("switches attempts with the selector and shows the code read-only", async () => {
    renderTab(run({ best_attempt_id: "a2" }));
    const editor = await screen.findByLabelText("Attempt code");
    expect(editor).toHaveAttribute("readonly");
    await userEvent.click(screen.getByRole("combobox", { name: "Attempt" }));
    await userEvent.click(
      await screen.findByRole("option", { name: /Attempt 1/ })
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Attempt code")).toHaveValue("// attempt 1")
    );
  });

  it("follows the dark theme", async () => {
    document.documentElement.classList.add("dark");
    renderTab(run());
    expect(await screen.findByLabelText("Attempt code")).toHaveAttribute(
      "data-theme",
      "vs-dark"
    );
  });

  it("hides the diff toggle without a base version", async () => {
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    expect(screen.queryByRole("button", { name: /Diff vs/ })).toBeNull();
  });

  it("diffs the attempt against the base version", async () => {
    const getVersion = vi
      .spyOn(versionsService, "getVersion")
      .mockResolvedValue({
        id: "v9",
        number: 4,
        code: "// base",
      } as versionsService.Version);
    renderTab(run({ base_version: { id: "v9", number: 4 } }));
    await screen.findByLabelText("Attempt code");
    expect(getVersion).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Diff vs v4" }));
    const diff = await screen.findByTestId("diff");
    expect(getVersion).toHaveBeenCalledWith("v9");
    expect(diff).toHaveAttribute("data-original", "// base");
    expect(diff).toHaveAttribute("data-modified", "// attempt 3");
    expect(screen.getByText("v4")).toBeInTheDocument();
    expect(screen.getAllByText("Attempt 3").length).toBeGreaterThan(0);
  });

  it("lists safety violations and the compile log", async () => {
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue([
      attempt(1, {
        safety_ok: false,
        safety_violations: [
          { line: 7, rule: "no_delay", message: "delay() is not allowed" },
        ],
        compile_ok: false,
        compile_log: "error: expected ';'",
      }),
    ]);
    renderTab(run());
    expect(await screen.findByText(/delay\(\) is not allowed/)).toBeVisible();
    expect(screen.getByText(/Line 7/)).toBeInTheDocument();
    expect(screen.getByText("no_delay")).toBeInTheDocument();
    expect(screen.getByText("Compile log")).toBeInTheDocument();
    expect(screen.getByText("error: expected ';'")).toBeInTheDocument();
  });

  it("omits the violation and log sections when there is nothing to show", async () => {
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    expect(screen.queryByText("Compile log")).toBeNull();
    expect(screen.queryByText(/Safety violations/)).toBeNull();
  });

  it("shows no actions unless the run failed with a confirmed spec", async () => {
    renderTab(run({ status: "succeeded" }));
    await screen.findByLabelText("Attempt code");
    expect(screen.queryByRole("button", { name: /Edit & run/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit scenarios/ })).toBeNull();
    cleanup();
    renderTab(run({ confirmed_spec: null }));
    await screen.findByLabelText("Attempt code");
    expect(screen.queryByRole("button", { name: /Edit & run/ })).toBeNull();
  });

  it("offers the scenario retry only for request runs", async () => {
    renderTab(run({ kind: "manual" }));
    await screen.findByLabelText("Attempt code");
    expect(
      screen.getByRole("button", { name: "Edit & run manually" })
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit scenarios/ })).toBeNull();
  });

  it("posts the edited code for a manual run", async () => {
    const spy = vi.spyOn(actions, "manualRun").mockResolvedValue(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    const editor = screen.getByLabelText("Attempt code");
    expect(editor).not.toHaveAttribute("readonly");
    expect(editor).toHaveValue("// attempt 3");
    fireEvent.change(editor, { target: { value: "// fixed" } });
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("r1", "// fixed", false)
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Attempt code")).toHaveAttribute("readonly")
    );
  });

  it("leaves manual editing on cancel without posting", async () => {
    const spy = vi.spyOn(actions, "manualRun").mockResolvedValue(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText("Attempt code")).toHaveAttribute("readonly");
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows the error of a rejected manual run", async () => {
    vi.spyOn(actions, "manualRun").mockRejectedValue(
      new ApiError(409, "RUN_ACTIVE", "A run is already working", {})
    );
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A run is already working"
    );
  });

  it("posts the edited spec in ms for a retry", async () => {
    const spy = vi.spyOn(actions, "retryRun").mockResolvedValue(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit scenarios & retry" })
    );
    const time = await screen.findByLabelText(
      "Scenario s1 step 1 time (seconds)"
    );
    await userEvent.clear(time);
    await userEvent.type(time, "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Run again" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [runId, sent, cancelWaiting] = spy.mock.calls[0]!;
    expect(runId).toBe("r1");
    expect(cancelWaiting).toBe(false);
    expect(sent.scenarios[0]?.steps[0]?.at_ms).toBe(2500);
    expect(sent.summary).toBe("Fan on when hot");
  });

  it("blocks the retry on client validation errors", async () => {
    const spy = vi.spyOn(actions, "retryRun").mockResolvedValue(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit scenarios & retry" })
    );
    const temp = await screen.findByLabelText("Scenario s1 step 1 temp value");
    await userEvent.clear(temp);
    await userEvent.type(temp, "99");
    expect(screen.getByRole("button", { name: "Run again" })).toBeDisabled();
    expect(
      screen.getAllByText(/temp must be between -10 and 50/).length
    ).toBeGreaterThan(0);
    expect(spy).not.toHaveBeenCalled();
  });

  it("lists the problems of a SPEC_INVALID retry", async () => {
    vi.spyOn(actions, "retryRun").mockRejectedValue(
      new ApiError(422, "SPEC_INVALID", "Spec invalid", {
        errors: [
          { path: "scenarios[0].steps[0].at_ms", message: "must be 0 first" },
        ],
      })
    );
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit scenarios & retry" })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Run again" })
    );
    expect(
      await screen.findByText("Scenario s1 › step 1 › at ms: must be 0 first")
    ).toBeInTheDocument();
  });

  it("clears the manual error when the code is edited", async () => {
    vi.spyOn(actions, "manualRun").mockRejectedValue(
      new ApiError(409, "RUN_ACTIVE", "A run is already working", {})
    );
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    await screen.findByRole("alert");
    fireEvent.change(screen.getByLabelText("Attempt code"), {
      target: { value: "// again" },
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps violations and the compile log visible while editing", async () => {
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue([
      attempt(1, {
        safety_violations: [
          { line: 7, rule: "no_delay", message: "delay() is not allowed" },
        ],
        compile_log: "error: expected ';'",
      }),
    ]);
    renderTab(run());
    await screen.findByText(/delay\(\) is not allowed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    expect(screen.getByText(/delay\(\) is not allowed/)).toBeInTheDocument();
    expect(screen.getByText("error: expected ';'")).toBeInTheDocument();
  });

  it("asks before cancelling a waiting run and resends a manual run on yes", async () => {
    const spy = vi
      .spyOn(actions, "manualRun")
      .mockRejectedValueOnce(waiting())
      .mockResolvedValueOnce(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    expect(await screen.findByText(/Cancel waiting run\?/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy).toHaveBeenNthCalledWith(1, "r1", "// attempt 3", false);
    expect(spy).toHaveBeenLastCalledWith("r1", "// attempt 3", true);
    await waitFor(() =>
      expect(screen.getByLabelText("Attempt code")).toHaveAttribute("readonly")
    );
  });

  it("sends nothing and stays in edit mode when the user keeps waiting", async () => {
    const spy = vi.spyOn(actions, "manualRun").mockRejectedValue(waiting());
    const { invalidate } = renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Keep waiting" })
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run this code" })
      ).toBeEnabled()
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Attempt code")).not.toHaveAttribute(
      "readonly"
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("asks before cancelling a waiting run and resends a retry on yes", async () => {
    const spy = vi
      .spyOn(actions, "retryRun")
      .mockRejectedValueOnce(waiting())
      .mockResolvedValueOnce(created);
    renderTab(run());
    await screen.findByLabelText("Attempt code");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit scenarios & retry" })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Run again" })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel it and continue" })
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[0]?.[2]).toBe(false);
    expect(spy.mock.calls[1]?.[2]).toBe(true);
  });

  it("keeps the selected attempt and refreshes the session after a manual run", async () => {
    vi.spyOn(actions, "manualRun").mockResolvedValue(created);
    const { invalidate } = renderTab(run({ best_attempt_id: "a2" }));
    await screen.findByLabelText("Attempt code");
    await userEvent.click(screen.getByRole("combobox", { name: "Attempt" }));
    await userEvent.click(
      await screen.findByRole("option", { name: /Attempt 1/ })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Edit & run manually" })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Run this code" })
    );
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] })
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Attempt code")).toHaveAttribute("readonly")
    );
    expect(screen.getByRole("combobox", { name: "Attempt" })).toHaveTextContent(
      "Attempt 1"
    );
    expect(screen.getByLabelText("Attempt code")).toHaveValue("// attempt 1");
  });

  it("keeps the selected attempt and refreshes the session after a retry", async () => {
    vi.spyOn(actions, "retryRun").mockResolvedValue(created);
    const { invalidate } = renderTab(run({ best_attempt_id: "a2" }));
    await screen.findByLabelText("Attempt code");
    await userEvent.click(screen.getByRole("combobox", { name: "Attempt" }));
    await userEvent.click(
      await screen.findByRole("option", { name: /Attempt 1/ })
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Edit scenarios & retry" })
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Run again" })
    );
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sessions", "s1"] })
    );
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Attempt" })
      ).toHaveTextContent("Attempt 1")
    );
  });
});
