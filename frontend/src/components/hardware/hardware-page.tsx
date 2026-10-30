"use client";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useHardware } from "@/hooks/queries/use-hardware";
import type {
  HardwareChannel,
  HardwareComponent,
} from "@/services/hardware.service";

function formatNeutral(value: HardwareChannel["neutral"]): string {
  if (value === null || value === "") return "—";
  return String(value);
}

function formatRange(range: HardwareChannel["range"]): string {
  return range && range.length === 2 ? `${range[0]} to ${range[1]}` : "—";
}

function formatDisplaySize(channel: HardwareChannel): string {
  return channel.rows != null && channel.cols != null
    ? `${channel.cols} × ${channel.rows}`
    : "—";
}

function dash(value: string | number | null): string {
  return value === null ? "—" : String(value);
}

function ComponentCard({ component }: { component: HardwareComponent }) {
  const pins = Object.entries(component.pins);
  return (
    <li className="border-border bg-card space-y-3 rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium">{component.name}</h3>
        <span className="text-muted-foreground text-xs capitalize">
          {component.kind}
        </span>
      </div>
      {pins.length > 0 && (
        <ul className="text-muted-foreground space-y-0.5 text-sm">
          {pins.map(([name, gpio]) => (
            <li key={name}>
              {name} → GPIO {gpio}
            </li>
          ))}
        </ul>
      )}
      <p className="text-sm">
        <span className="text-muted-foreground">Channels: </span>
        {component.channels.join(", ")}
      </p>
      {component.ai_notes && (
        <AiNotes name={component.name} notes={component.ai_notes} />
      )}
    </li>
  );
}

// The notes are short Markdown bullet lists; lines without a "- " continue the bullet above.
function noteItems(notes: string): string[] {
  const items: string[] = [];
  for (const raw of notes.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("- ") || items.length === 0) {
      items.push(line.replace(/^- /, ""));
    } else {
      items[items.length - 1] += ` ${line}`;
    }
  }
  return items;
}

function withCode(text: string): React.ReactNode[] {
  return text.split(/(`[^`]+`)/).map((part, i) =>
    part.startsWith("`") && part.endsWith("`") && part.length > 1 ? (
      <code key={i} className="text-foreground font-mono text-[0.85em]">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    )
  );
}

function AiNotes({ name, notes }: { name: string; notes: string }) {
  const items = noteItems(notes);
  if (!notes.trimStart().startsWith("- ")) {
    return (
      <p className="text-muted-foreground text-sm">
        {withCode(items.join(" "))}
      </p>
    );
  }
  return (
    <ul
      aria-label={`${name} notes`}
      className="text-muted-foreground flex list-disc flex-col gap-1.5 pl-4 text-sm marker:text-current/40"
    >
      {items.map((item, i) => (
        <li key={i}>{withCode(item)}</li>
      ))}
    </ul>
  );
}

function ChannelsTable({ channels }: { channels: HardwareChannel[] }) {
  const heads = [
    "Id",
    "Kind",
    "Type",
    "Unit",
    "Range",
    "Resolution",
    "Neutral",
    "Default hysteresis",
    "Injectable",
    "Display size",
  ];
  return (
    <div
      role="region"
      aria-label="Channels"
      tabIndex={0}
      className="border-border bg-card focus-visible:ring-ring/50 overflow-x-auto rounded-xl border outline-none focus-visible:ring-3"
    >
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Channels</caption>
        <thead className="text-muted-foreground border-border border-b text-xs">
          <tr>
            {heads.map((head) => (
              <th
                key={head}
                scope="col"
                className="px-4 py-3 font-medium whitespace-nowrap"
              >
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-border divide-y">
          {channels.map((channel) => (
            <tr key={channel.id}>
              <th scope="row" className="px-4 py-3 font-medium">
                {channel.id}
              </th>
              <td className="px-4 py-3 capitalize">{channel.kind}</td>
              <td className="px-4 py-3">{channel.type}</td>
              <td className="px-4 py-3">{dash(channel.unit)}</td>
              <td className="px-4 py-3 whitespace-nowrap">
                {formatRange(channel.range)}
              </td>
              <td className="px-4 py-3">{dash(channel.resolution)}</td>
              <td className="px-4 py-3">{formatNeutral(channel.neutral)}</td>
              <td className="px-4 py-3">{dash(channel.default_hysteresis)}</td>
              <td className="px-4 py-3">{channel.injectable ? "Yes" : "No"}</td>
              <td className="px-4 py-3 whitespace-nowrap">
                {formatDisplaySize(channel)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HardwareSkeleton() {
  return (
    <div
      className="space-y-4"
      role="status"
      aria-label="Loading hardware"
      aria-busy="true"
    >
      {Array.from({ length: 3 }, (_, i) => (
        <Skeleton key={i} className="h-24 w-full rounded-xl" />
      ))}
    </div>
  );
}

export function HardwarePage() {
  const hardware = useHardware();

  let body;
  if (hardware.isPending) {
    body = <HardwareSkeleton />;
  } else if (hardware.isError) {
    body = (
      <div className="flex flex-col items-center gap-4 px-4 py-12 text-center">
        <p role="alert" className="text-muted-foreground text-sm">
          {hardware.error.message}
        </p>
        <Button variant="outline" onClick={() => void hardware.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else {
    body = (
      <div className="space-y-10">
        <section className="space-y-4">
          <h2 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Components
          </h2>
          <ul className="grid gap-4 sm:grid-cols-2">
            {hardware.data.components.map((component) => (
              <ComponentCard key={component.id} component={component} />
            ))}
          </ul>
        </section>
        <section className="space-y-4">
          <h2 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Channels
          </h2>
          <ChannelsTable channels={hardware.data.channels} />
        </section>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-8 sm:py-14">
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">Hardware</h1>
      {body}
    </div>
  );
}
