import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TestsTab } from "@/components/tests/tests-tab";
import * as attemptsService from "@/services/attempts.service";
import * as testsService from "@/services/run-tests.service";
import type { Run } from "@/services/runs.service";

type Attempt = attemptsService.Attempt;
type TestCase = testsService.TestCase;
type TestResult = testsService.TestResult;
type Expectation = NonNullable<TestResult["failures"]>[number]["expectation"];

const testCase = (id: string, over: Partial<TestCase> = {}): TestCase => ({
  id,
  origin: "scenario",
  from_base: false,
  scenario: { id: `sc-${id}`, title: `Title ${id}`, steps: [] },
  ...over,
});

const attempt = (number: number, results: TestResult[]): Attempt =>
  ({
    id: `a${number}`,
    number,
    test_results: results,
    tests_total: results.length,
  }) as unknown as Attempt;

const run = (over: Partial<Run> = {}): Run =>
  ({ id: "r1", session_id: "s1", best_attempt_id: null, ...over }) as Run;

const failure = {
  step_index: 1,
  message: "fan should be on within 500 ms",
  observed_summary: "fan: t=1000..1300 -> 0,0,0",
  expectation: {
    channel: "fan",
    op: "eq" as const,
    value: true,
    hold_ms: 0,
    tolerance: 0,
    within_ms: 500,
  },
};

const renderTab = (r: Run) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TestsTab run={r} sessionId="s1" />
    </QueryClientProvider>
  );

describe("TestsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const mock = (cases: TestCase[], attempts: Attempt[]) => {
    vi.spyOn(testsService, "listRunTests").mockResolvedValue(cases);
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue(attempts);
  };

  it("shows an empty state before tests exist", async () => {
    mock([], []);
    renderTab(run());
    expect(await screen.findByText(/no tests yet/i)).toBeInTheDocument();
  });

  it("shows every test as not run when there are no attempt results", async () => {
    mock([testCase("t1"), testCase("t2")], []);
    renderTab(run());
    expect(await screen.findByText("Title t1")).toBeInTheDocument();
    expect(screen.getAllByText("Not run")).toHaveLength(2);
  });

  it("joins cases with the results of the latest attempt by default", async () => {
    mock(
      [
        testCase("t1"),
        testCase("t2", { origin: "boundary" }),
        testCase("t3", { origin: "truth_table" }),
      ],
      [
        attempt(1, [{ test_id: "t1", passed: false, failures: [failure] }]),
        attempt(2, [
          { test_id: "t1", passed: true },
          { test_id: "t2", passed: false, failures: [failure] },
        ]),
      ]
    );
    renderTab(run());
    expect(await screen.findByText("1 of 3 passed")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("Passed")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Scenario")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Failed")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Boundary")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Not run")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Truth table")).toBeInTheDocument();
  });

  it("defaults to the best attempt and switches attempt on request", async () => {
    mock(
      [testCase("t1")],
      [
        attempt(1, [{ test_id: "t1", passed: true }]),
        attempt(2, [{ test_id: "t1", passed: false, failures: [failure] }]),
      ]
    );
    renderTab(run({ best_attempt_id: "a1" }));
    expect(await screen.findByText("1 of 1 passed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Attempt" }));
    await userEvent.click(
      await screen.findByRole("option", { name: /Attempt 2/ })
    );
    await waitFor(() =>
      expect(screen.getByText("0 of 1 passed")).toBeInTheDocument()
    );
  });

  it("expands a failed row to show step, message, expected and observed", async () => {
    mock(
      [testCase("t1")],
      [attempt(1, [{ test_id: "t1", passed: false, failures: [failure] }])]
    );
    renderTab(run());
    const toggle = await screen.findByRole("button", { name: /t1/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Step 2")).toBeInTheDocument();
    expect(
      screen.getByText("fan should be on within 500 ms")
    ).toBeInTheDocument();
    expect(screen.getByText("fan is true within 500 ms")).toBeInTheDocument();
    expect(screen.getByText("fan: t=1000..1300 -> 0,0,0")).toBeInTheDocument();
  });

  it.each<[string, Partial<Expectation>, string]>([
    [
      "a value source",
      { channel: "fan", op: "eq", source_channel: "temp", value: null },
      "fan is temp",
    ],
    [
      "a non-eq op and a row",
      { channel: "display", op: "display_contains", row: 1, value: "HOT" },
      "display row 1 contains HOT",
    ],
    [
      "hold and tolerance",
      { channel: "temp", op: "eq", value: 25, hold_ms: 200, tolerance: 0.5 },
      "temp is 25 for 200 ms ±0.5",
    ],
  ])("words the expectation for %s", async (_name, patch, text) => {
    const f = {
      ...failure,
      expectation: { ...failure.expectation, within_ms: 0, ...patch },
    };
    mock(
      [testCase("t1")],
      [attempt(1, [{ test_id: "t1", passed: false, failures: [f] }])]
    );
    renderTab(run());
    await userEvent.click(await screen.findByRole("button", { name: /t1/ }));
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("links the expand button to its detail row", async () => {
    mock(
      [testCase("t1")],
      [attempt(1, [{ test_id: "t1", passed: false, failures: [failure] }])]
    );
    renderTab(run());
    const toggle = await screen.findByRole("button", { name: /t1/ });
    await userEvent.click(toggle);
    const id = toggle.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toContainElement(
      screen.getByText("Step 2")
    );
  });

  it("shows the case error", async () => {
    mock(
      [testCase("t1")],
      [attempt(1, [{ test_id: "t1", passed: false, error: "TIMEOUT" }])]
    );
    renderTab(run());
    await userEvent.click(await screen.findByRole("button", { name: /t1/ }));
    expect(screen.getByText("TIMEOUT")).toBeInTheDocument();
  });

  it("badges regression tests from the base", async () => {
    mock([testCase("t1", { from_base: true }), testCase("t2")], []);
    renderTab(run());
    await screen.findByText("Title t1");
    expect(screen.getAllByText("From base")).toHaveLength(1);
  });

  it("puts the table in a labelled, focusable scroll region", async () => {
    mock([testCase("t1")], []);
    renderTab(run());
    const region = await screen.findByRole("region", { name: "Tests" });
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("shows an error when loading fails", async () => {
    vi.spyOn(testsService, "listRunTests").mockRejectedValue(new Error("boom"));
    vi.spyOn(attemptsService, "listRunAttempts").mockResolvedValue([]);
    renderTab(run());
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("offers no expand button for passed and not-run rows", async () => {
    mock(
      [testCase("t1"), testCase("t2")],
      [attempt(1, [{ test_id: "t1", passed: true }])]
    );
    renderTab(run());
    await screen.findByText("Title t1");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(within(row).queryByRole("button")).toBeNull();
    }
  });

  it("collapses an expanded row on a second click", async () => {
    mock(
      [testCase("t1")],
      [attempt(1, [{ test_id: "t1", passed: false, failures: [failure] }])]
    );
    renderTab(run());
    const toggle = await screen.findByRole("button", { name: /t1/ });
    await userEvent.click(toggle);
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Step 2")).toBeNull();
  });

  it("lists every failure of a case with its own step number", async () => {
    const second = {
      ...failure,
      step_index: 3,
      message: "display should say HOT",
      observed_summary: "display: empty",
    };
    mock(
      [testCase("t1")],
      [
        attempt(1, [
          { test_id: "t1", passed: false, failures: [failure, second] },
        ]),
      ]
    );
    renderTab(run());
    await userEvent.click(await screen.findByRole("button", { name: /t1/ }));
    expect(screen.getByText("Step 2")).toBeInTheDocument();
    expect(screen.getByText("Step 4")).toBeInTheDocument();
    expect(screen.getByText("display: empty")).toBeInTheDocument();
    expect(screen.getByText("fan: t=1000..1300 -> 0,0,0")).toBeInTheDocument();
  });

  it("shows the id in a monospace cell and the scenario title", async () => {
    mock([testCase("t1")], []);
    renderTab(run());
    const id = await screen.findByText("t1");
    expect(id.className).toContain("font-mono");
    expect(screen.getByText("Title t1")).toBeInTheDocument();
  });

  it("shows the table headers id, origin, title and result", async () => {
    mock([testCase("t1")], []);
    renderTab(run());
    await screen.findByText("Title t1");
    for (const name of ["ID", "Origin", "Title", "Result"]) {
      expect(
        screen.getByRole("columnheader", { name: new RegExp(`^${name}$`, "i") })
      ).toBeInTheDocument();
    }
  });
});
