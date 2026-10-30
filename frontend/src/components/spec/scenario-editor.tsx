"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";

import { NumberField } from "@/components/spec/number-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  MAX_STEPS,
  msToSeconds,
  secondsToMs,
  validateScenarios,
  type Expectation,
  type Scenario,
  type SpecIssue,
  type Step,
} from "@/lib/spec-edit";
import type { HardwareChannel } from "@/services/hardware.service";

interface ScenarioEditorProps {
  value: Scenario[];
  onChange: (value: Scenario[]) => void;
  channels: HardwareChannel[];
}

type Op = Expectation["op"];

const OP_LABELS: Record<Op, string> = {
  eq: "is",
  display_contains: "contains",
  display_row_eq: "row shows",
  display_contains_value: "contains value of",
};

export function opsForKind(kind: HardwareChannel["kind"]): Op[] {
  if (kind === "actuator") return ["eq"];
  if (kind === "display") {
    return ["display_contains", "display_row_eq", "display_contains_value"];
  }
  return [];
}

const isNumeric = (c: HardwareChannel) =>
  c.type === "float" || c.type === "int";

function defaultValue(channel: HardwareChannel): number | boolean {
  if (channel.type === "bool") return false;
  if (typeof channel.neutral === "number") return channel.neutral;
  return channel.range?.[0] ?? 0;
}

function newExpectation(
  channel: HardwareChannel,
  previous?: Expectation
): Expectation {
  const base = {
    channel: channel.id,
    hold_ms: previous?.hold_ms ?? 0,
    within_ms: previous?.within_ms ?? 0,
    tolerance: 0,
  };
  if (channel.kind === "display") {
    return { ...base, op: "display_contains", value: "" };
  }
  return { ...base, op: "eq", value: true };
}

/** Switch operator, clearing only the fields the new operator does not use. */
function withOp(
  expectation: Expectation,
  op: Op,
  firstNumericSensor: string | null
): Expectation {
  const next: Expectation = {
    ...expectation,
    op,
    value: op === "display_contains_value" ? null : expectation.value,
    tolerance: op === "display_contains_value" ? expectation.tolerance : 0,
    row: op === "display_row_eq" ? (expectation.row ?? 0) : null,
    source_channel:
      op === "display_contains_value"
        ? (expectation.source_channel ?? firstNumericSensor)
        : null,
  };
  if (op !== "display_contains_value" && typeof next.value !== "string") {
    next.value = "";
  }
  return next;
}

function ChoiceSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string | null;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <Select
      value={value}
      items={options}
      onValueChange={(v) => {
        if (typeof v === "string") onChange(v);
      }}
    >
      <SelectTrigger aria-label={label} className="min-w-28">
        <SelectValue placeholder={placeholder ?? "Choose"} />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ValueInput({
  label,
  channel,
  value,
  onChange,
}: {
  label: string;
  channel: HardwareChannel;
  value: number | boolean;
  onChange: (value: number | boolean) => void;
}) {
  if (channel.type === "bool") {
    return (
      <span className="inline-flex items-center gap-2 text-sm">
        <Switch
          aria-label={label}
          checked={value === true}
          onCheckedChange={(checked) => onChange(checked)}
        />
        <span className="text-muted-foreground w-6">
          {value === true ? "on" : "off"}
        </span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <NumberField
        label={label}
        className="w-24"
        value={typeof value === "number" ? value : 0}
        min={channel.range?.[0]}
        max={channel.range?.[1]}
        step={channel.resolution ?? (channel.type === "int" ? 1 : undefined)}
        onCommit={onChange}
      />
      {channel.unit ? (
        <span className="text-muted-foreground text-xs">{channel.unit}</span>
      ) : null}
    </span>
  );
}

function ValueMap({
  title,
  prefix,
  entries,
  sensors,
  onChange,
}: {
  title: string;
  prefix: string;
  entries: Record<string, number | boolean>;
  sensors: HardwareChannel[];
  onChange: (next: Record<string, number | boolean>) => void;
}) {
  const unused = sensors.filter((s) => !(s.id in entries));
  return (
    <div className="flex flex-col gap-2">
      <span className="text-muted-foreground text-xs font-medium">{title}</span>
      {Object.entries(entries).map(([name, v]) => {
        const channel = sensors.find((s) => s.id === name);
        return (
          <div key={name} className="flex flex-wrap items-center gap-2">
            <span className="w-20 text-sm font-medium">{name}</span>
            {channel ? (
              <ValueInput
                label={`${prefix} ${name} value`}
                channel={channel}
                value={v}
                onChange={(next) => onChange({ ...entries, [name]: next })}
              />
            ) : (
              <span className="text-muted-foreground text-sm">{String(v)}</span>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-8"
              aria-label={`${prefix} remove ${name}`}
              onClick={() => {
                const rest = { ...entries };
                delete rest[name];
                onChange(rest);
              }}
            >
              <Trash2Icon />
            </Button>
          </div>
        );
      })}
      {unused.length > 0 ? (
        <ChoiceSelect
          label={`${prefix} add input`}
          value={null}
          placeholder="Add input…"
          options={unused.map((s) => ({ value: s.id, label: s.id }))}
          onChange={(id) => {
            const channel = unused.find((s) => s.id === id);
            if (channel) {
              onChange({ ...entries, [id]: defaultValue(channel) });
            }
          }}
        />
      ) : null}
    </div>
  );
}

function TimingField({
  label,
  caption,
  ms,
  onChange,
}: {
  label: string;
  caption: string;
  ms: number;
  onChange: (ms: number) => void;
}) {
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
      {caption}
      <NumberField
        label={label}
        className="w-16"
        min={0}
        step={0.1}
        value={msToSeconds(ms)}
        onCommit={(seconds) => onChange(secondsToMs(seconds))}
      />
      s
    </span>
  );
}

function ExpectationRow({
  label,
  expectation,
  outputs,
  sensors,
  onChange,
  onDelete,
}: {
  label: string;
  expectation: Expectation;
  outputs: HardwareChannel[];
  sensors: HardwareChannel[];
  onChange: (next: Expectation) => void;
  onDelete: () => void;
}) {
  const channel = outputs.find((c) => c.id === expectation.channel);
  const ops = channel ? opsForKind(channel.kind) : [];
  const numericSensors = sensors.filter(isNumeric);
  return (
    <div className="bg-muted/40 flex flex-wrap items-center gap-2 rounded-lg p-2">
      <ChoiceSelect
        label={`${label} channel`}
        value={expectation.channel || null}
        options={outputs.map((c) => ({ value: c.id, label: c.id }))}
        onChange={(id) => {
          const next = outputs.find((c) => c.id === id);
          if (next) onChange(newExpectation(next, expectation));
        }}
      />
      {channel && ops.length > 1 ? (
        <ChoiceSelect
          label={`${label} operator`}
          value={expectation.op}
          options={ops.map((o) => ({ value: o, label: OP_LABELS[o] }))}
          onChange={(op) =>
            onChange(
              withOp(expectation, op as Op, numericSensors[0]?.id ?? null)
            )
          }
        />
      ) : channel ? (
        <span className="text-muted-foreground text-sm">
          {OP_LABELS[expectation.op]}
        </span>
      ) : null}
      {channel?.kind === "actuator" ? (
        <ValueInput
          label={`${label} value`}
          channel={channel}
          value={expectation.value === true}
          onChange={(v) => onChange({ ...expectation, value: v })}
        />
      ) : null}
      {expectation.op === "display_row_eq" ? (
        <NumberField
          label={`${label} row`}
          className="w-16"
          min={0}
          max={channel?.rows ? channel.rows - 1 : undefined}
          step={1}
          value={expectation.row ?? 0}
          onCommit={(row) => onChange({ ...expectation, row })}
        />
      ) : null}
      {expectation.op === "display_contains" ||
      expectation.op === "display_row_eq" ? (
        <Input
          aria-label={`${label} text`}
          className="w-44"
          value={typeof expectation.value === "string" ? expectation.value : ""}
          onChange={(e) => onChange({ ...expectation, value: e.target.value })}
        />
      ) : null}
      {expectation.op === "display_contains_value" ? (
        <>
          <ChoiceSelect
            label={`${label} source sensor`}
            value={expectation.source_channel ?? null}
            options={numericSensors.map((c) => ({ value: c.id, label: c.id }))}
            onChange={(id) => onChange({ ...expectation, source_channel: id })}
          />
          <span className="text-muted-foreground text-sm">±</span>
          <NumberField
            label={`${label} tolerance`}
            className="w-20"
            min={0}
            step={0.1}
            value={expectation.tolerance}
            onCommit={(tolerance) => onChange({ ...expectation, tolerance })}
          />
        </>
      ) : null}
      <TimingField
        label={`${label} within (s)`}
        caption="within"
        ms={expectation.within_ms}
        onChange={(within_ms) => onChange({ ...expectation, within_ms })}
      />
      <TimingField
        label={`${label} hold (s)`}
        caption="hold"
        ms={expectation.hold_ms}
        onChange={(hold_ms) => onChange({ ...expectation, hold_ms })}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Delete ${label}`}
        onClick={onDelete}
        className="ml-auto size-8"
      >
        <Trash2Icon />
      </Button>
    </div>
  );
}

function StepCard({
  label,
  step,
  sensors,
  outputs,
  issues,
  onChange,
  onDelete,
}: {
  label: string;
  step: Step;
  sensors: HardwareChannel[];
  outputs: HardwareChannel[];
  issues: SpecIssue[];
  onChange: (next: Step) => void;
  onDelete: () => void;
}) {
  return (
    <div className="border-border flex flex-col gap-3 rounded-xl border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">At</span>
        <NumberField
          label={`${label} time (seconds)`}
          className="w-24"
          min={0}
          step={0.1}
          value={msToSeconds(step.at_ms)}
          onCommit={(s) => onChange({ ...step, at_ms: secondsToMs(s) })}
        />
        <span className="text-muted-foreground text-sm">s</span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Delete ${label}`}
          onClick={onDelete}
          className="ml-auto size-8"
        >
          <Trash2Icon />
        </Button>
      </div>
      <ValueMap
        title="Set inputs"
        prefix={label}
        entries={step.set ?? {}}
        sensors={sensors}
        onChange={(set) => onChange({ ...step, set })}
      />
      <div className="flex flex-col gap-2">
        <span className="text-muted-foreground text-xs font-medium">
          Expect
        </span>
        {step.expect.map((e, i) => (
          <ExpectationRow
            key={i}
            label={`${label} expectation ${i + 1}`}
            expectation={e}
            outputs={outputs}
            sensors={sensors}
            onChange={(next) =>
              onChange({
                ...step,
                expect: step.expect.map((x, j) => (j === i ? next : x)),
              })
            }
            onDelete={() =>
              onChange({
                ...step,
                expect: step.expect.filter((_, j) => j !== i),
              })
            }
          />
        ))}
        {outputs.length > 0 ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() =>
              onChange({
                ...step,
                expect: [...step.expect, newExpectation(outputs[0]!)],
              })
            }
          >
            <PlusIcon /> Add expectation
            <span className="sr-only"> to {label}</span>
          </Button>
        ) : null}
      </div>
      {issues.length > 0 ? (
        <ul className="text-destructive list-disc pl-5 text-sm">
          {issues.map((issue, i) => (
            <li key={i}>{issue.message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ScenarioEditor({
  value,
  onChange,
  channels,
}: ScenarioEditorProps) {
  const sensors = channels.filter((c) => c.kind === "sensor");
  const outputs = channels.filter((c) => c.kind !== "sensor");
  const issues = validateScenarios(value, channels);

  const update = (index: number, next: Scenario) =>
    onChange(value.map((s, i) => (i === index ? next : s)));

  return (
    <div className="flex flex-col gap-6">
      {value.map((scenario, si) => {
        const own = issues.filter((i) => i.scenarioId === scenario.id);
        const scenarioLabel = `Scenario ${scenario.id}`;
        return (
          <section
            key={scenario.id}
            aria-label={scenarioLabel}
            className="flex flex-col gap-3"
          >
            <Input
              aria-label={`${scenarioLabel} title`}
              className="h-9 text-base font-medium"
              value={scenario.title}
              onChange={(e) =>
                update(si, { ...scenario, title: e.target.value })
              }
            />
            <ValueMap
              title="Starting inputs"
              prefix={`${scenarioLabel} given`}
              entries={scenario.given ?? {}}
              sensors={sensors}
              onChange={(given) => update(si, { ...scenario, given })}
            />
            {scenario.steps.map((step, i) => (
              <StepCard
                key={i}
                label={`${scenarioLabel} step ${i + 1}`}
                step={step}
                sensors={sensors}
                outputs={outputs}
                issues={own.filter((x) => x.stepIndex === i)}
                onChange={(next) =>
                  update(si, {
                    ...scenario,
                    steps: scenario.steps.map((s, j) => (j === i ? next : s)),
                  })
                }
                onDelete={() =>
                  update(si, {
                    ...scenario,
                    steps: scenario.steps.filter((_, j) => j !== i),
                  })
                }
              />
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              disabled={scenario.steps.length >= MAX_STEPS}
              onClick={() => {
                const last = scenario.steps[scenario.steps.length - 1];
                update(si, {
                  ...scenario,
                  steps: [
                    ...scenario.steps,
                    {
                      at_ms: last ? last.at_ms + 1000 : 0,
                      set: {},
                      expect: [],
                    },
                  ],
                });
              }}
            >
              <PlusIcon /> Add step
              <span className="sr-only"> to {scenarioLabel}</span>
            </Button>
            {own
              .filter((x) => x.stepIndex === null)
              .map((issue, i) => (
                <p key={i} className="text-destructive text-sm">
                  {issue.message}
                </p>
              ))}
          </section>
        );
      })}
    </div>
  );
}
