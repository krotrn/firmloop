import {
  CheckCircle2Icon,
  CircleDashedIcon,
  HourglassIcon,
  Loader2Icon,
  XCircleIcon,
  type LucideIcon,
} from "lucide-react";

import type { ChatWaitState } from "@/hooks/use-chat-wait";
import { isTerminal } from "@/lib/run-status";
import { runSteps, type RunStep, type StepState } from "@/lib/run-steps";
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

const CHAT_STEP_KEY = "chat" as const;

/**
 * The steps with the chat wait inserted right after the step it happens in. A wait on a run that
 * already ended is dropped; an answered one is shown done.
 */
function withChatStep(
  steps: RunStep[],
  chatWait: ChatWaitState,
  runStatus: string
): Array<RunStep | (Omit<RunStep, "key"> & { key: typeof CHAT_STEP_KEY })> {
  const waiting = chatWait === "waiting" && !isTerminal(runStatus);
  if (!waiting && chatWait !== "answered") return steps;
  const chat = {
    key: CHAT_STEP_KEY,
    label: "Waiting for chat",
    state: (waiting ? "current" : "done") as StepState,
  };
  let at = steps.findIndex(
    (s) => s.state === "current" || s.state === "waiting"
  );
  if (at < 0) at = steps.findIndex((s) => s.state === "failed");
  if (at < 0) return [...steps, chat];
  return [...steps.slice(0, at + 1), chat, ...steps.slice(at + 1)];
}

export function RunTimeline({
  run,
  deviceMode,
  chatWait = "idle",
}: {
  run: Run;
  deviceMode: DeviceStatus["mode"] | null;
  chatWait?: ChatWaitState;
}) {
  const steps = withChatStep(runSteps(run, deviceMode), chatWait, run.status);
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
