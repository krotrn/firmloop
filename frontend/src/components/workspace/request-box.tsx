"use client";

import { Loader2Icon, SendIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useCreateRun } from "@/hooks/queries/use-runs";
import { isWorking } from "@/lib/run-status";
import { useCancelWaiting } from "@/providers/cancel-waiting-provider";

const MAX_LENGTH = 2000;
const COUNTER_FROM = 1800;

export function RequestBox({
  sessionId,
  latestRunStatus,
  onSent,
}: {
  sessionId: string;
  latestRunStatus: string | null;
  onSent: () => void;
}) {
  const [text, setText] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const createRun = useCreateRun(sessionId);
  const { runWithCancelWaiting } = useCancelWaiting();
  const inputId = React.useId();

  const trimmed = text.trim();
  const runInProgress = latestRunStatus !== null && isWorking(latestRunStatus);
  const canSend =
    trimmed.length > 0 &&
    trimmed.length <= MAX_LENGTH &&
    !sending &&
    !runInProgress;

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    setError(null);
    try {
      const run = await runWithCancelWaiting((cancelWaiting) =>
        createRun.mutateAsync({ requestText: trimmed, cancelWaiting })
      );
      // No run means the user chose to keep the waiting run: nothing was sent.
      if (run === undefined) return;
      setText("");
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send the request");
    } finally {
      setSending(false);
    }
  };

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        Request
      </label>
      <Textarea
        id={inputId}
        value={text}
        placeholder="What should the board do?"
        rows={3}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            void send();
          }
        }}
      />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="text-muted-foreground flex items-center gap-3 text-sm">
          {runInProgress && <span>A run is in progress.</span>}
          {text.length > COUNTER_FROM && (
            <span
              className={
                trimmed.length > MAX_LENGTH
                  ? "text-destructive tabular-nums"
                  : "tabular-nums"
              }
            >
              {text.length}/{MAX_LENGTH}
            </span>
          )}
        </div>
        <Button type="submit" disabled={!canSend}>
          {sending ? (
            <Loader2Icon className="animate-spin" data-icon="inline-start" />
          ) : (
            <SendIcon data-icon="inline-start" />
          )}
          Send
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
    </form>
  );
}
