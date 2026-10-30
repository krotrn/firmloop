import type { components } from "@/types/generated/api";

export type Scenario = components["schemas"]["Scenario"];
export type Step = components["schemas"]["Step"];
export type Expectation = components["schemas"]["Expectation"];
export type ChannelLike = Pick<
  components["schemas"]["ChannelOut"],
  "id" | "kind" | "type" | "range" | "resolution" | "rows" | "cols"
>;

export const MAX_STEPS = 20;

export const msToSeconds = (ms: number): number => ms / 1000;

export const secondsToMs = (seconds: number): number =>
  Math.round(seconds * 1000);

export function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every(
    (k) =>
      Object.prototype.hasOwnProperty.call(b, k) &&
      deepEqual(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k]
      )
  );
}

export const wasEdited = (original: Scenario[], current: Scenario[]): boolean =>
  !deepEqual(original, current);

export interface SpecIssue {
  scenarioId: string;
  stepIndex: number | null;
  message: string;
}

const isNumericType = (type: string) => type === "float" || type === "int";

function checkValue(
  channel: ChannelLike | undefined,
  name: string,
  value: unknown
): string | null {
  if (!channel) return `Unknown channel "${name}".`;
  if (channel.type === "bool") {
    return typeof value === "boolean" ? null : `${name} needs on or off.`;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return `${name} needs a number.`;
  }
  if (channel.type === "int" && !Number.isInteger(value)) {
    return `${name} must be a whole number.`;
  }
  const range = channel.range;
  if (range && range.length === 2 && (value < range[0]! || value > range[1]!)) {
    return `${name} must be between ${range[0]} and ${range[1]}.`;
  }
  return null;
}

export const MAX_DURATION_MS = 120_000;
export const TAIL_MS = 200;

function scenarioEndMs(scenario: Scenario): number {
  let end = 0;
  for (const step of scenario.steps) {
    end = Math.max(end, step.at_ms);
    for (const e of step.expect) {
      end = Math.max(end, step.at_ms + (e.within_ms ?? 0) + (e.hold_ms ?? 0));
    }
  }
  return end + TAIL_MS;
}

export function validateScenarios(
  scenarios: Scenario[],
  channels: ChannelLike[]
): SpecIssue[] {
  const byId = new Map(channels.map((c) => [c.id, c]));
  const checkChannels = channels.length > 0;
  const issues: SpecIssue[] = [];
  for (const scenario of scenarios) {
    const add = (stepIndex: number | null, message: string) =>
      issues.push({ scenarioId: scenario.id, stepIndex, message });
    const checkInput = (
      stepIndex: number | null,
      name: string,
      value: unknown
    ) => {
      if (!checkChannels) return;
      const channel = byId.get(name);
      if (channel && channel.kind !== "sensor") {
        add(stepIndex, `${name} is not a sensor, so it cannot be set.`);
        return;
      }
      const msg = checkValue(channel, name, value);
      if (msg) add(stepIndex, msg);
    };
    if (!scenario.title.trim()) add(null, "The title is required.");
    for (const [name, value] of Object.entries(scenario.given ?? {})) {
      checkInput(null, name, value);
    }
    if (scenario.steps.length < 1) add(null, "Add at least one step.");
    if (scenario.steps.length > MAX_STEPS) {
      add(null, `Use at most ${MAX_STEPS} steps.`);
    }
    if (scenarioEndMs(scenario) > MAX_DURATION_MS) {
      add(null, "The scenario is longer than 120 seconds.");
    }
    scenario.steps.forEach((step, i) => {
      if (!Number.isFinite(step.at_ms) || step.at_ms < 0) {
        add(i, "The time must be 0 or more.");
      } else if (i > 0 && step.at_ms <= scenario.steps[i - 1]!.at_ms) {
        add(i, "The time must be later than the previous step.");
      }
      for (const [name, value] of Object.entries(step.set ?? {})) {
        checkInput(i, name, value);
      }
      for (const e of step.expect) {
        if (!e.channel) {
          add(i, "Choose a channel for every expectation.");
          continue;
        }
        if (!(e.within_ms >= 0) || !(e.hold_ms >= 0)) {
          add(i, "Within and hold must be 0 or more.");
        }
        if (!checkChannels) continue;
        const channel = byId.get(e.channel);
        if (!channel) {
          add(i, `Unknown channel "${e.channel}".`);
          continue;
        }
        if (channel.kind === "sensor") {
          add(i, `${e.channel} is a sensor; expect an output or the display.`);
          continue;
        }
        if (e.op === "eq") {
          if (channel.kind !== "actuator") {
            add(i, `${e.channel} cannot be compared with "is".`);
          }
          const msg = checkValue(channel, e.channel, e.value);
          if (msg) add(i, msg);
          continue;
        }
        if (channel.kind !== "display") {
          add(i, `${e.channel} is not the display.`);
          continue;
        }
        if (e.op === "display_contains") {
          if (typeof e.value !== "string" || e.value === "") {
            add(i, "Enter the text the display should contain.");
          }
        } else if (e.op === "display_row_eq") {
          if (typeof e.value !== "string" || e.value === "") {
            add(i, "Enter the text the display row should show.");
          }
          const rows = channel.rows ?? null;
          if (
            e.row == null ||
            !Number.isInteger(e.row) ||
            e.row < 0 ||
            (rows !== null && e.row >= rows)
          ) {
            add(i, "Choose a valid display row.");
          }
        } else if (e.op === "display_contains_value") {
          const source = e.source_channel
            ? byId.get(e.source_channel)
            : undefined;
          if (!source || !isNumericType(source.type)) {
            add(i, "Choose a numeric sensor to compare with the display.");
          }
          if (!Number.isFinite(e.tolerance) || e.tolerance < 0) {
            add(i, "The tolerance must be 0 or more.");
          }
        }
      }
    });
  }
  return issues;
}
