"use client";

import { MoreHorizontalIcon, PencilIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useArchiveSession,
  useUpdateSession,
} from "@/hooks/queries/use-sessions";
import { cn } from "@/lib/utils";
import type { DeviceStatus } from "@/services/device.service";
import type { Session } from "@/services/sessions.service";

const MAX_TITLE = 200;

function TitleEditor({ session }: { session: Session }) {
  const [editing, setEditing] = React.useState(false);
  const update = useUpdateSession(session.id);
  // A field that is closed by Enter, Escape or a successful save must not save again
  // from the blur that follows.
  const finishedRef = React.useRef(false);
  // A failed value is not sent again by a later blur until it is edited.
  const attemptedRef = React.useRef<string | null>(null);

  const open = () => {
    finishedRef.current = false;
    attemptedRef.current = null;
    update.reset();
    setEditing(true);
  };

  const close = () => {
    finishedRef.current = true;
    update.reset();
    setEditing(false);
  };

  const save = async (value: string) => {
    if (finishedRef.current || update.isPending) return;
    const title = value.trim();
    if (title === "" || title === session.title) {
      close();
      return;
    }
    if (title === attemptedRef.current) return;
    attemptedRef.current = title;
    try {
      await update.mutateAsync({ title });
      close();
    } catch {
      // The failure is shown inline from the mutation state.
    }
  };

  if (!editing) {
    return (
      <h1 className="-mx-2 max-w-full min-w-0">
        <button
          type="button"
          onClick={open}
          title="Rename session"
          aria-label={`Rename session: ${session.title}`}
          className="group hover:bg-muted/60 focus-visible:ring-ring/50 flex max-w-full min-w-0 items-center gap-2 rounded-lg px-2 py-1 text-left text-2xl font-semibold tracking-tight transition-colors outline-none focus-visible:ring-[3px]"
        >
          <span className="truncate">{session.title}</span>
          <PencilIcon
            aria-hidden="true"
            className="text-muted-foreground size-4 shrink-0 opacity-60 group-hover:opacity-100"
          />
        </button>
      </h1>
    );
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <Input
        autoFocus
        aria-label="Session title"
        defaultValue={session.title}
        maxLength={MAX_TITLE}
        aria-invalid={update.isError || undefined}
        className="h-10 text-lg font-semibold"
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            void save(event.currentTarget.value);
          } else if (event.key === "Escape") {
            event.preventDefault();
            close();
          }
        }}
        onBlur={(event) => void save(event.currentTarget.value)}
      />
      {update.isError && (
        <p role="alert" className="text-destructive text-sm">
          {update.error.message}
        </p>
      )}
    </div>
  );
}

function ArchiveMenu({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const archive = useArchiveSession(sessionId);
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  const confirm = async () => {
    try {
      await archive.mutateAsync();
      router.push("/");
    } catch {
      // The failure is shown inline in the dialog.
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon" aria-label="Session actions" />
          }
        >
          <MoreHorizontalIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-auto">
          <DropdownMenuItem
            onClick={() => {
              archive.reset();
              setConfirmOpen(true);
            }}
          >
            Archive session
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Archive this session?</DialogTitle>
            <DialogDescription>
              It leaves the sessions list. Its versions stay.
            </DialogDescription>
          </DialogHeader>
          {archive.isError && (
            <p role="alert" className="text-destructive text-sm">
              {archive.error.message}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void confirm()}
              disabled={archive.isPending || archive.isSuccess}
            >
              Archive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function DeviceLine({
  device,
  isPending,
  isError,
}: {
  device: DeviceStatus | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  if (isPending) {
    return <Skeleton aria-hidden="true" className="h-5 w-56" />;
  }
  if (!device) {
    return (
      <p className="text-muted-foreground text-sm">
        {isError ? "Device status unavailable" : null}
      </p>
    );
  }
  return (
    <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <span className="border-border text-foreground rounded-full border px-2 py-0.5 text-xs font-medium">
        {device.mode}
      </span>
      <span className="flex items-center gap-1.5">
        <span
          aria-hidden="true"
          className={cn(
            "size-2 rounded-full",
            device.connected ? "bg-green-500" : "bg-muted-foreground/40"
          )}
        />
        {device.connected ? "connected" : "disconnected"}
      </span>
      {device.mode === "real" && device.port && (
        <span
          className="max-w-48 truncate font-mono text-xs"
          title={device.port}
        >
          {device.port}
        </span>
      )}
      {!device.running_version && <span>Board: no program</span>}
    </div>
  );
}

function BoardOwnerNotice({
  device,
  sessionId,
}: {
  device: DeviceStatus | undefined;
  sessionId: string;
}) {
  const running = device?.running_version;
  if (!running || running.session_id === sessionId) return null;
  const title = running.session_title ?? "another session";
  const label = running.number != null ? `${title} v${running.number}` : title;
  return (
    <p
      role="status"
      className="border-border bg-muted/50 rounded-lg border px-3 py-2 text-sm"
    >
      Board runs{" "}
      <Link
        href={`/sessions/${encodeURIComponent(running.session_id)}`}
        className="font-medium underline underline-offset-4"
      >
        {label}
      </Link>
      . The next deploy will replace it.
    </p>
  );
}

export function SessionHeader({
  session,
  device,
  deviceIsPending,
  deviceIsError,
}: {
  session: Session;
  device: DeviceStatus | undefined;
  deviceIsPending: boolean;
  deviceIsError: boolean;
}) {
  const number = session.current_version?.number;
  return (
    <header className="mb-8 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 basis-60 items-center">
          <TitleEditor session={session} />
        </div>
        <span className="text-muted-foreground text-sm tabular-nums">
          {number != null ? `v${number}` : "No version yet"}
        </span>
        <ArchiveMenu sessionId={session.id} />
      </div>
      <DeviceLine
        device={device}
        isPending={deviceIsPending}
        isError={deviceIsError}
      />
      <BoardOwnerNotice device={device} sessionId={session.id} />
    </header>
  );
}
