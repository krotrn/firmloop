"use client";

import { Loader2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function RollbackDialog({
  versionNumber,
  pending,
  onConfirm,
  onClose,
}: {
  versionNumber: number | null;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const label = versionNumber != null ? `v${versionNumber}` : "this version";
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Roll back to {label}?</DialogTitle>
          <DialogDescription>
            The board is re-flashed with {label}; in virtual mode it is
            reloaded.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={pending}>
            {pending && (
              <Loader2Icon className="animate-spin" data-icon="inline-start" />
            )}
            Roll back
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
