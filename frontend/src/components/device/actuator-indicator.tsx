import { cn } from "@/lib/utils";

export interface ActuatorIndicatorProps {
  id: string;
  on: boolean;
}

export function ActuatorIndicator({ id, on }: ActuatorIndicatorProps) {
  const state = on ? "on" : "off";
  return (
    <div
      role="group"
      aria-label={`${id} ${state}`}
      className="flex items-center justify-between gap-3 rounded-xl border p-4"
    >
      <span className="text-sm font-medium">{id}</span>
      <span className="flex items-center gap-2 text-sm">
        <span
          aria-hidden
          className={cn(
            "size-2.5 rounded-full transition-colors motion-reduce:transition-none",
            on ? "bg-primary" : "bg-muted-foreground/30"
          )}
        />
        <span className={on ? "text-foreground" : "text-muted-foreground"}>
          {state}
        </span>
      </span>
    </div>
  );
}
