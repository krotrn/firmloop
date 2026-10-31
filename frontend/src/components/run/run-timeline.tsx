import {
  CheckCircle2Icon,
  CircleDashedIcon,
  HourglassIcon,
  Loader2Icon,
  XCircleIcon,
  type LucideIcon,
} from "lucide-react";

import { runSteps, type StepState } from "@/lib/run-steps";
import { cn } from "@/lib/utils";
import type { DeviceStatus } from "@/services/device.service";
import type { Run } from "@/services/runs.service";

const STATE_VIEW: Record<
  StepState,
  { Icon: LucideIcon; text: string; className: string; spin?: boolean }
> = {
  done: { Icon: CheckCircle2Icon, text: "Done", className: "text-foreground" },
  current: {
    Icon: Loader2Icon,
    text: "In progress",
    className: "text-foreground",
    spin: true,
  },
  waiting: {
    Icon: HourglassIcon,
    text: "Waiting for you",
    className: "text-foreground",
  },
  failed: {
    Icon: XCircleIcon,
    text: "Failed",
    className: "text-destructive",
  },
  pending: {
    Icon: CircleDashedIcon,
    text: "Pending",
    className: "text-muted-foreground",
  },
};

export function RunTimeline({
  run,
  deviceMode,
}: {
  run: Run;
  deviceMode: DeviceStatus["mode"] | null;
}) {
  const steps = runSteps(run, deviceMode);
  return (
    <section
      aria-labelledby="run-timeline-heading"
      className="flex flex-col gap-3"
    >
      <h3 id="run-timeline-heading" className="text-sm font-medium">
        Steps
      </h3>
      <ol className="flex flex-col gap-2">
        {steps.map((step) => {
          const view = STATE_VIEW[step.state];
          const text =
            step.state === "failed" && run.status === "cancelled"
              ? "Stopped"
              : view.text;
          const active = step.state === "current" || step.state === "waiting";
          return (
            <li
              key={step.key}
              aria-current={active ? "step" : undefined}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-1 text-sm",
                view.className
              )}
            >
              <view.Icon
                aria-hidden="true"
                className={cn(
                  "size-4 shrink-0",
                  view.spin && "motion-safe:animate-spin"
                )}
              />
              <span className={cn(active && "font-medium")}>{step.label}</span>
              {step.note && (
                <span className="text-muted-foreground text-xs">
                  ({step.note})
                </span>
              )}
              <span className="text-muted-foreground text-xs">{text}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
