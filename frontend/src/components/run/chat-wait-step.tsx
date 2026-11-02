import { CheckCircle2Icon, Loader2Icon } from "lucide-react";

import type { ChatWaitState } from "@/hooks/use-chat-wait";
import { isTerminal } from "@/lib/run-status";

/**
 * The "Waiting for chat" step of a run whose LLM steps a chat app answers. Shown running while a
 * task waits and done after it was answered; a run that already ended never shows it running.
 */
export function ChatWaitStep({
  state,
  runStatus,
}: {
  state: ChatWaitState;
  runStatus: string;
}) {
  const waiting = state === "waiting" && !isTerminal(runStatus);
  if (!waiting && state !== "answered") return null;
  return (
    <ol className="flex flex-col gap-2" aria-label="Chat app">
      <li
        aria-current={waiting ? "step" : undefined}
        className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
      >
        {waiting ? (
          <Loader2Icon
            aria-hidden="true"
            className="size-4 shrink-0 motion-safe:animate-spin"
          />
        ) : (
          <CheckCircle2Icon aria-hidden="true" className="size-4 shrink-0" />
        )}
        <span className={waiting ? "font-medium" : undefined}>
          Waiting for chat
        </span>
        <span className="text-muted-foreground text-xs">
          {waiting ? "In progress" : "Done"}
        </span>
      </li>
    </ol>
  );
}
