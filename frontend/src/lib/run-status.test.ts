import { describe, expect, it } from "vitest";

import {
  TERMINAL_STATUSES,
  WAITING_STATUSES,
  WORKING_STATUSES,
  isTerminal,
  isWaiting,
  isWorking,
} from "@/lib/run-status";

describe("run status groups", () => {
  it("lists the working statuses", () => {
    expect([...WORKING_STATUSES]).toEqual([
      "pending",
      "understanding",
      "generating_tests",
      "coding",
      "checking",
      "native_testing",
      "building",
      "flashing",
      "verifying",
    ]);
  });

  it("lists the waiting and terminal statuses", () => {
    expect([...WAITING_STATUSES]).toEqual([
      "awaiting_answer",
      "awaiting_confirmation",
    ]);
    expect([...TERMINAL_STATUSES]).toEqual([
      "succeeded",
      "failed",
      "cancelled",
    ]);
  });

  it("classifies each status into exactly one group", () => {
    for (const s of WORKING_STATUSES) {
      expect([isWorking(s), isWaiting(s), isTerminal(s)]).toEqual([
        true,
        false,
        false,
      ]);
    }
    for (const s of WAITING_STATUSES) {
      expect([isWorking(s), isWaiting(s), isTerminal(s)]).toEqual([
        false,
        true,
        false,
      ]);
    }
    for (const s of TERMINAL_STATUSES) {
      expect([isWorking(s), isWaiting(s), isTerminal(s)]).toEqual([
        false,
        false,
        true,
      ]);
    }
  });

  it("puts unknown statuses in no group", () => {
    for (const s of ["mystery", "", "constructor"]) {
      expect([isWorking(s), isWaiting(s), isTerminal(s)]).toEqual([
        false,
        false,
        false,
      ]);
    }
  });
});
