"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { readActiveRun, sameSnapshot, snapshotOf, type BulkStartSnapshot } from "@/lib/ai-studio/bulk";
import { requestIdOf, studioErrorMessage } from "@/lib/ai-studio/errors";
import { useStartPipelineRun } from "@/services/products";
import type { Store } from "@/services/stores";
import type { AITone, PipelineBulkRun } from "@/types/api";

const TONES: AITone[] = ["professional", "persuasive", "luxury", "technical", "friendly"];

function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Confirm and start one bulk preview run (plan §19, §23).
 *
 * One idempotency key per frozen snapshot (sorted unique ids, tone, store).
 * A double click, a retry button or a network retry of the same snapshot
 * resends the same key and body, so the server replays the same run instead
 * of starting a second one. Changing tone, store or selection is a new intent
 * and mints a new key — reusing the old one would be a 409 payload mismatch.
 */
export function BulkStartDialog({
  open,
  onOpenChange,
  productIds,
  stores,
  tenantId,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  productIds: readonly string[];
  stores: Store[];
  tenantId: string | null;
  onStarted: (run: PipelineBulkRun) => void;
}) {
  const [tone, setTone] = useState<AITone>("professional");
  const [storeId, setStoreId] = useState<string>("");
  const start = useStartPipelineRun();
  const intent = useRef<{ snapshot: BulkStartSnapshot; key: string } | null>(null);

  function submit() {
    if (start.isPending || productIds.length === 0) return;
    const snapshot = snapshotOf(productIds, tone, storeId || null);
    if (intent.current === null || !sameSnapshot(intent.current.snapshot, snapshot)) {
      intent.current = { snapshot, key: newKey() };
    }
    const { key } = intent.current;
    start.mutate(
      {
        productIds: snapshot.productIds,
        idempotencyKey: key,
        tone: snapshot.tone,
        ...(snapshot.storeId ? { storeId: snapshot.storeId } : {}),
      },
      {
        onSuccess: (run) => {
          // A later dialog is a new intent with a new key.
          intent.current = null;
          onStarted(run);
        },
      },
    );
  }

  const activeConflict = start.error instanceof ApiError && start.error.code === "pipeline_bulk_run_active";
  const storedRun = activeConflict && tenantId ? readActiveRun(tenantId) : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) start.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent data-testid="ai-studio-bulk-dialog">
        <DialogHeader>
          <DialogTitle>Generate previews for {productIds.length} products?</DialogTitle>
          <DialogDescription>
            Each product gets an AI candidate to review. Nothing is approved or published automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="ai-studio-bulk-tone">Tone</Label>
            <select
              id="ai-studio-bulk-tone"
              className="h-10 w-full rounded-md border bg-background px-3 text-sm capitalize"
              value={tone}
              onChange={(event) => setTone(event.target.value as AITone)}
            >
              {TONES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="ai-studio-bulk-store">Store (optional)</Label>
            <select
              id="ai-studio-bulk-store"
              className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              value={storeId}
              onChange={(event) => setStoreId(event.target.value)}
            >
              <option value="">No store</option>
              {stores.map((store) => (
                <option key={store.id} value={store.id}>
                  {store.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {start.isError ? (
          <Alert variant="destructive" data-testid="ai-studio-bulk-error">
            <AlertDescription>
              {activeConflict ? (
                storedRun ? (
                  <>
                    A bulk optimization is already running.{" "}
                    <Link className="underline" href={`/ai-studio?run=${storedRun}`}>
                      Open that run
                    </Link>
                  </>
                ) : (
                  "Another bulk optimization is already running for this workspace. This browser did not start it, so it cannot open it here."
                )
              ) : (
                studioErrorMessage(start.error)
              )}
              {requestIdOf(start.error) ? (
                <span className="block text-xs">Reference: {requestIdOf(start.error)}</span>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Not now
          </Button>
          <Button
            type="button"
            onClick={submit}
            disabled={start.isPending || activeConflict}
            data-testid="ai-studio-bulk-confirm"
          >
            {start.isPending ? "Starting…" : start.isError && !activeConflict ? "Try again" : "Start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
