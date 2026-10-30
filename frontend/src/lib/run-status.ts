export const WORKING_STATUSES = [
  "pending",
  "understanding",
  "generating_tests",
  "coding",
  "checking",
  "native_testing",
  "building",
  "flashing",
  "verifying",
] as const;

export const WAITING_STATUSES = [
  "awaiting_answer",
  "awaiting_confirmation",
] as const;

export const TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"] as const;

export const isWorking = (status: string): boolean =>
  (WORKING_STATUSES as readonly string[]).includes(status);

export const isWaiting = (status: string): boolean =>
  (WAITING_STATUSES as readonly string[]).includes(status);

export const isTerminal = (status: string): boolean =>
  (TERMINAL_STATUSES as readonly string[]).includes(status);
