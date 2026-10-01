"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Approval confirmation (plan §14, review finding G-1).
 *
 * The copy states exactly what approval does. The earlier "This replaces the
 * current product text" was false: approval never writes the draft and never
 * contacts Shopify.
 */
export function ApproveDialog({
  open,
  onOpenChange,
  onConfirm,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="ai-studio-approve-dialog">
        <DialogHeader>
          <DialogTitle>Approve this AI version?</DialogTitle>
          <DialogDescription data-testid="ai-studio-approve-dialog-body">
            Makes this the approved AI version. Your current draft text remains unchanged. Nothing
            changes on Shopify until you publish.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Not now
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            data-testid="ai-studio-approve-confirm"
          >
            {pending ? "Approving…" : "Approve version"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
