"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isApiError } from "@/lib/fetcher";
import { sessionsService } from "@/services/sessions.service";

type RunWithCancelWaiting = <T>(
  send: (cancelWaiting: boolean) => Promise<T>
) => Promise<T | undefined>;

const FALLBACK_TITLE = "another session";

const CancelWaitingContext = React.createContext<{
  runWithCancelWaiting: RunWithCancelWaiting;
} | null>(null);

async function titleFor(sessionId: unknown): Promise<string> {
  if (typeof sessionId !== "string") return FALLBACK_TITLE;
  try {
    return (
      (await sessionsService.getSession(sessionId)).title || FALLBACK_TITLE
    );
  } catch {
    return FALLBACK_TITLE;
  }
}

export function CancelWaitingProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  // Kept after closing so the text does not change during the close animation.
  const [sessionTitle, setSessionTitle] = React.useState(FALLBACK_TITLE);
  const resolverRef = React.useRef<((confirmed: boolean) => void) | null>(null);
  const latestRef = React.useRef(0);

  const settle = React.useCallback((confirmed: boolean) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    resolve?.(confirmed);
  }, []);

  // A confirmation still open when the provider goes away counts as "Keep waiting".
  React.useEffect(() => () => settle(false), [settle]);

  const confirm = React.useCallback(
    async (sessionId: unknown): Promise<boolean> => {
      // The newest confirmation wins, in call order: an open or still-loading older one
      // resolves as "Keep waiting".
      const token = ++latestRef.current;
      settle(false);
      const title = await titleFor(sessionId);
      if (token !== latestRef.current) return false;
      return new Promise<boolean>((resolve) => {
        resolverRef.current = resolve;
        setSessionTitle(title);
        setOpen(true);
      });
    },
    [settle]
  );

  const runWithCancelWaiting = React.useCallback<RunWithCancelWaiting>(
    async (send) => {
      try {
        return await send(false);
      } catch (e) {
        if (!isApiError(e, "RUN_WAITING")) throw e;
        if (!(await confirm(e.details.session_id))) return undefined;
        return await send(true);
      }
    },
    [confirm]
  );

  const answer = (confirmed: boolean) => {
    setOpen(false);
    settle(confirmed);
  };

  const value = React.useMemo(
    () => ({ runWithCancelWaiting }),
    [runWithCancelWaiting]
  );

  return (
    <CancelWaitingContext.Provider value={value}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(open) => {
          if (!open) answer(false);
        }}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Cancel waiting run?</DialogTitle>
            <DialogDescription>
              A run in {sessionTitle} is waiting for you. Cancel it and
              continue?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => answer(false)}>
              Keep waiting
            </Button>
            <Button onClick={() => answer(true)}>Cancel it and continue</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </CancelWaitingContext.Provider>
  );
}

export function useCancelWaiting() {
  const context = React.useContext(CancelWaitingContext);
  if (!context) {
    throw new Error(
      "useCancelWaiting must be used inside CancelWaitingProvider"
    );
  }
  return context;
}
