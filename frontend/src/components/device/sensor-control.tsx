"use client";

import * as React from "react";

import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { formatChannelValue } from "@/components/device/format";
import type { HardwareChannel } from "@/services/hardware.service";

export interface SensorControlProps {
  channel: HardwareChannel;
  /** The value to show: the latest telemetry value, or the channel's neutral value. */
  value: number;
  /** Real mode: show the value only. Disabled: controls are inert (a test is running). */
  readOnly?: boolean;
  disabled?: boolean;
  onCommit: (value: boolean | number) => void;
}

function firstValue(value: number | readonly number[]): number | null {
  return typeof value === "number" ? value : (value[0] ?? null);
}

const HOLD_MS = 1000;

interface HeldValue {
  value: number;
  until: number;
}

function matches(channel: HardwareChannel, telemetry: number, held: number) {
  if (channel.type === "bool") return telemetry >= 0.5 === held >= 0.5;
  return Math.abs(telemetry - held) <= (channel.resolution ?? 1) / 2;
}

export function SensorControl({
  channel,
  value,
  readOnly = false,
  disabled = false,
  onCommit,
}: SensorControlProps) {
  const labelId = React.useId();
  const [dragValue, setDragValue] = React.useState<number | null>(null);
  const [held, setHeld] = React.useState<HeldValue | null>(null);

  // Adjusting state during render: a drag cannot outlive the control being
  // enabled, and a held value ends once telemetry reports it.
  if (disabled && dragValue !== null) setDragValue(null);
  if (held !== null && matches(channel, value, held.value)) setHeld(null);

  React.useEffect(() => {
    if (held === null) return;
    const timer = setTimeout(
      () => setHeld(null),
      Math.max(0, held.until - Date.now())
    );
    return () => clearTimeout(timer);
  }, [held]);

  const isBool = channel.type === "bool";
  const shown = dragValue ?? held?.value ?? value;
  const valueText = formatChannelValue(shown, channel);

  const commit = (next: number | boolean) => {
    const numeric =
      typeof next === "boolean"
        ? next
          ? 1
          : 0
        : channel.type === "int"
          ? Math.round(next)
          : next;
    setHeld({ value: numeric, until: Date.now() + HOLD_MS });
    onCommit(typeof next === "boolean" ? next : numeric);
  };
  const [min, max] = channel.range ?? [0, 100];

  return (
    <div className="flex flex-col gap-3 rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-3">
        <span id={labelId} className="text-sm font-medium">
          {channel.id}
        </span>
        {isBool ? (
          <span className="text-muted-foreground text-sm">
            {shown >= 0.5 ? "on" : "off"}
          </span>
        ) : (
          <span className="text-sm tabular-nums">{valueText}</span>
        )}
      </div>
      {readOnly ? null : isBool ? (
        <Switch
          aria-labelledby={labelId}
          checked={shown >= 0.5}
          disabled={disabled}
          onCheckedChange={(checked) => commit(checked)}
        />
      ) : (
        <div>
          <Slider
            aria-labelledby={labelId}
            min={min}
            max={max}
            step={channel.resolution ?? 1}
            value={[shown]}
            getAriaValueText={() => valueText}
            disabled={disabled}
            onValueChange={(next) => {
              setDragValue(firstValue(next));
            }}
            onValueCommitted={(next) => {
              setDragValue(null);
              const committed = firstValue(next);
              if (committed !== null) commit(committed);
            }}
          />
        </div>
      )}
    </div>
  );
}
