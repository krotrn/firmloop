"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";

interface NumberFieldProps {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  className?: string;
}

/** Number input that keeps what the user is typing and commits only finite numbers. */
export function NumberField({
  label,
  value,
  onCommit,
  min,
  max,
  step,
  className,
}: NumberFieldProps) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <Input
      type="number"
      inputMode="decimal"
      aria-label={label}
      className={`tabular-nums ${className ?? ""}`}
      min={min}
      max={max}
      step={step}
      value={draft ?? String(value)}
      onChange={(e) => {
        setDraft(e.target.value);
        const parsed = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isFinite(parsed)) {
          onCommit(parsed);
        }
      }}
      onBlur={() => setDraft(null)}
    />
  );
}
