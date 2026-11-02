"use client";

import { Loader2Icon, TriangleAlertIcon } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { ChatConnect } from "@/components/settings/chat-connect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useHealthQuery } from "@/hooks/queries/use-health";
import { useSettings, useUpdateSettings } from "@/hooks/queries/use-settings";
import type { Settings, SettingsUpdate } from "@/services/settings.service";

type Provider = Settings["llm_provider"];
type DeviceMode = Settings["device_mode"];

const NO_FALLBACK = "none";
const PROVIDER_LABELS: Partial<Record<Provider, string>> = {
  chat: "Chat app (MCP)",
};
const providerLabel = (p: Provider): string => PROVIDER_LABELS[p] ?? p;
const MIN_ATTEMPTS = 1;
const MAX_ATTEMPTS = 8;

interface Edits {
  llm_provider?: Provider;
  llm_model?: string;
  llm_fallback_provider?: Provider | null;
  max_attempts?: string;
  auto_approve?: boolean;
  device_mode?: DeviceMode;
  voice_input_enabled?: boolean;
  speak_results?: boolean;
}

function parseAttempts(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text);
  return value >= MIN_ATTEMPTS && value <= MAX_ATTEMPTS ? value : null;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {title}
      </h2>
      <div className="border-border bg-card divide-border divide-y rounded-xl border">
        {children}
      </div>
    </section>
  );
}

function Row({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
      <div className="min-w-0 space-y-1">
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </label>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </div>
      <div className="sm:w-56 sm:shrink-0">{children}</div>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-6 px-4 py-4">
      <div className="min-w-0 space-y-1">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function SettingsSkeleton() {
  return (
    <div
      className="space-y-4"
      role="status"
      aria-label="Loading settings"
      aria-busy="true"
    >
      {Array.from({ length: 4 }, (_, i) => (
        <Skeleton key={i} className="h-16 w-full rounded-xl" />
      ))}
    </div>
  );
}

function SettingsForm({ settings }: { settings: Settings }) {
  const update = useUpdateSettings();
  const [edits, setEdits] = useState<Edits>({});
  const [saved, setSaved] = useState(false);
  const [fallbackCleared, setFallbackCleared] = useState(false);
  const ids = {
    provider: useId(),
    model: useId(),
    fallback: useId(),
    attempts: useId(),
    mode: useId(),
    attemptsError: useId(),
    modelError: useId(),
  };

  const edit = (patch: Edits) => {
    setEdits((current) => ({ ...current, ...patch }));
    setSaved(false);
    update.reset();
  };

  const provider = edits.llm_provider ?? settings.llm_provider;
  const model = edits.llm_model ?? settings.llm_model;
  const trimmedModel = model.trim();
  const fallback =
    edits.llm_fallback_provider !== undefined
      ? edits.llm_fallback_provider
      : settings.llm_fallback_provider;
  const attemptsText = edits.max_attempts ?? String(settings.max_attempts);
  const attempts = parseAttempts(attemptsText);
  const deviceMode = edits.device_mode ?? settings.device_mode;
  const autoApprove = edits.auto_approve ?? settings.auto_approve;
  const voiceInput = edits.voice_input_enabled ?? settings.voice_input_enabled;
  const speak = edits.speak_results ?? settings.speak_results;

  const providers = Array.from(
    new Set<Provider>([...settings.available_providers, settings.llm_provider])
  );
  const fallbackChoices = providers.filter((p) => p !== provider);
  const providerItems = providers.map((p) => ({
    value: p,
    label: providerLabel(p),
  }));
  const fallbackItems = [
    { value: NO_FALLBACK, label: "None" },
    ...(fallback && !fallbackChoices.includes(fallback)
      ? [{ value: fallback, label: providerLabel(fallback) }]
      : []),
    ...fallbackChoices.map((p) => ({ value: p, label: providerLabel(p) })),
  ];
  const modeItems = [
    { value: "virtual", label: "Virtual" },
    { value: "real", label: "Real" },
  ];

  const changes: SettingsUpdate = {};
  if (provider !== settings.llm_provider) changes.llm_provider = provider;
  if (trimmedModel !== settings.llm_model) changes.llm_model = trimmedModel;
  if (fallback !== settings.llm_fallback_provider) {
    changes.llm_fallback_provider = fallback;
  }
  if (attempts !== null && attempts !== settings.max_attempts) {
    changes.max_attempts = attempts;
  }
  if (autoApprove !== settings.auto_approve) {
    changes.auto_approve = autoApprove;
  }
  if (deviceMode !== settings.device_mode) changes.device_mode = deviceMode;
  if (voiceInput !== settings.voice_input_enabled) {
    changes.voice_input_enabled = voiceInput;
  }
  if (speak !== settings.speak_results) changes.speak_results = speak;

  const modelInvalid = trimmedModel === "";
  const attemptsInvalid = attempts === null;
  const dirty = Object.keys(changes).length > 0;
  const canSave =
    dirty && !modelInvalid && !attemptsInvalid && !update.isPending;

  const save = async () => {
    const sent = edits;
    try {
      await update.mutateAsync(changes);
      // Keep anything typed while the save was in flight.
      setEdits((current) => {
        const rest: Record<string, unknown> = { ...current };
        for (const [key, value] of Object.entries(sent)) {
          if (rest[key] === value) delete rest[key];
        }
        return rest as Edits;
      });
      setFallbackCleared(false);
      setSaved(true);
    } catch {
      // The failure is shown inline from the mutation state.
    }
  };

  const reset = () => {
    setEdits({});
    setFallbackCleared(false);
    setSaved(false);
    update.reset();
  };

  return (
    <form
      className="space-y-8"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) void save();
      }}
    >
      <Section title="Model">
        <Row label="Provider" htmlFor={ids.provider}>
          <Select
            items={providerItems}
            value={provider}
            onValueChange={(value) => {
              if (!value) return;
              const next = value as Provider;
              if (next === fallback) {
                edit({ llm_provider: next, llm_fallback_provider: null });
                setFallbackCleared(true);
              } else {
                edit({ llm_provider: next });
              }
            }}
          >
            <SelectTrigger id={ids.provider} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {providerItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Row>
        {provider === "chat" && <ChatConnect />}
        <Row label="Model" htmlFor={ids.model}>
          <Input
            id={ids.model}
            value={model}
            onChange={(event) => edit({ llm_model: event.target.value })}
            aria-invalid={modelInvalid}
            aria-describedby={modelInvalid ? ids.modelError : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          {modelInvalid && (
            <p id={ids.modelError} className="text-destructive mt-1.5 text-xs">
              Enter a model name.
            </p>
          )}
        </Row>
        <Row
          label="Fallback provider"
          htmlFor={ids.fallback}
          hint={
            fallbackCleared
              ? "Fallback set to None because it matches the new provider."
              : "Used when the main provider is unavailable."
          }
        >
          <Select
            items={fallbackItems}
            value={fallback ?? NO_FALLBACK}
            onValueChange={(value) => {
              if (!value) return;
              edit({
                llm_fallback_provider:
                  value === NO_FALLBACK ? null : (value as Provider),
              });
            }}
          >
            <SelectTrigger id={ids.fallback} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {fallbackItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Row>
      </Section>

      <Section title="Runs">
        <Row
          label="Max attempts"
          htmlFor={ids.attempts}
          hint="How many code versions a run may try."
        >
          <Input
            id={ids.attempts}
            type="number"
            inputMode="numeric"
            min={MIN_ATTEMPTS}
            max={MAX_ATTEMPTS}
            step={1}
            value={attemptsText}
            onChange={(event) => edit({ max_attempts: event.target.value })}
            aria-invalid={attemptsInvalid}
            aria-describedby={attemptsInvalid ? ids.attemptsError : undefined}
          />
          {attemptsInvalid && (
            <p
              id={ids.attemptsError}
              className="text-destructive mt-1.5 text-xs"
            >
              Enter a whole number from {MIN_ATTEMPTS} to {MAX_ATTEMPTS}.
            </p>
          )}
        </Row>
        <ToggleRow
          label="Auto-approve"
          hint="Start building as soon as the spec is ready."
          checked={autoApprove}
          onChange={(value) => edit({ auto_approve: value })}
        />
      </Section>

      <Section title="Device">
        <Row
          label="Device mode"
          htmlFor={ids.mode}
          hint={
            deviceMode === "real"
              ? "Real mode needs the board; runs fail without it."
              : "Virtual mode runs everything without a board."
          }
        >
          <Select
            items={modeItems}
            value={deviceMode}
            onValueChange={(value) => {
              if (value) edit({ device_mode: value as DeviceMode });
            }}
          >
            <SelectTrigger id={ids.mode} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {modeItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Row>
      </Section>

      <Section title="Voice">
        <ToggleRow
          label="Voice input"
          checked={voiceInput}
          onChange={(value) => edit({ voice_input_enabled: value })}
        />
        <ToggleRow
          label="Speak results"
          checked={speak}
          onChange={(value) => edit({ speak_results: value })}
        />
      </Section>

      {update.isError && (
        <p role="alert" className="text-destructive text-sm">
          {update.error.message}
        </p>
      )}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!canSave}>
          {update.isPending && (
            <Loader2Icon className="animate-spin" data-icon="inline-start" />
          )}
          Save
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={reset}
          disabled={!dirty || update.isPending}
        >
          Reset
        </Button>
        {saved && !dirty && (
          <p role="status" className="text-muted-foreground text-sm">
            Saved
          </p>
        )}
      </div>
    </form>
  );
}

function HealthWarnings() {
  const health = useHealthQuery();
  const warnings = health.data?.warnings ?? [];
  if (warnings.length === 0) return null;
  return (
    <div
      role="status"
      className="border-border bg-muted/50 mb-8 space-y-1.5 rounded-xl border px-4 py-3"
    >
      {warnings.map((warning, index) => (
        <p
          key={`${index}-${warning}`}
          className="flex items-start gap-2 text-sm"
        >
          <TriangleAlertIcon
            aria-hidden="true"
            className="text-muted-foreground mt-0.5 size-4 shrink-0"
          />
          <span>{warning}</span>
        </p>
      ))}
    </div>
  );
}

export function SettingsPage() {
  const settings = useSettings();

  let body;
  if (settings.isPending) {
    body = <SettingsSkeleton />;
  } else if (settings.isError) {
    body = (
      <div className="flex flex-col items-center gap-4 px-4 py-12 text-center">
        <p role="alert" className="text-muted-foreground text-sm">
          {settings.error.message}
        </p>
        <Button variant="outline" onClick={() => void settings.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else {
    body = <SettingsForm settings={settings.data} />;
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-8 sm:py-14">
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">Settings</h1>
      <HealthWarnings />
      {body}
    </div>
  );
}
