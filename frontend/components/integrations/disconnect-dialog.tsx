"use client";

import { Loader2 } from "lucide-react";
import { useLayoutEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface DisconnectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Provider name for the title and the confirm button. */
  provider: string;
  /** The account or store being disconnected, when known. */
  identity?: string | null;
  /** What actually happens, in the merchant's terms — from the backend's behaviour. */
  consequences: string[];
  pending: boolean;
  error?: string | null;
  onConfirm: () => void;
  id: string;
}

/**
 * Disconnecting deletes the stored authorization, so it is a decision, not a
 * click. The dialog states what the backend really does — nothing more: no
 * product is deleted by any disconnect in this application, and the copy
 * must not imply otherwise.
 */
export function DisconnectDialog({
  open,
  onOpenChange,
  provider,
  identity,
  consequences,
  pending,
  error,
  onConfirm,
  id,
}: DisconnectDialogProps) {
  // Controlled and trigger-less, so Radix has nowhere to return focus to on
  // close. Remember what was focused when the dialog was asked to open —
  // a layout effect runs before Radix's own focus move — and go back there.
  const openerRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (open) openerRef.current = document.activeElement as HTMLElement | null;
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <DialogContent
        data-testid={`disconnect-dialog-${id}`}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          openerRef.current?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>
            Disconnect {provider}
            {identity ? ` — ${identity}` : ""}?
          </DialogTitle>
          <DialogDescription>This removes DropPilot&rsquo;s access. Here is what changes:</DialogDescription>
        </DialogHeader>
        <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
          {consequences.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" className="min-h-11 sm:min-h-9" disabled={pending} onClick={() => onOpenChange(false)}>
            Keep connected
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="min-h-11 sm:min-h-9"
            disabled={pending}
            onClick={onConfirm}
            data-testid={`disconnect-confirm-${id}`}
          >
            {pending ? <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : null}
            Disconnect {provider}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
