"use client";

import { CheckIcon, CopyIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

export const CHAT_CONNECT_COMMAND =
  "claude mcp add --transport http firmloop http://127.0.0.1:8000/mcp";

/** How to connect a chat app to FirmLoop when the chat provider is selected. */
export function ChatConnect() {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(CHAT_CONNECT_COMMAND);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="space-y-2 px-4 py-4">
      <div className="flex items-center justify-between gap-3">
        <code className="bg-muted min-w-0 rounded-md px-2 py-1 text-xs break-all">
          {CHAT_CONNECT_COMMAND}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void copy()}
        >
          {copied ? (
            <CheckIcon data-icon="inline-start" />
          ) : (
            <CopyIcon data-icon="inline-start" />
          )}
          {copied ? "Copied" : "Copy command"}
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        Then ask Claude Code to build something; this page shows the run live.
      </p>
    </div>
  );
}
