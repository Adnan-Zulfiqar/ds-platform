import type { AITone, PipelineBulkRun, PipelineBulkRunStatus } from "@/types/api";

/** The Stage 9 per-run cap. The backend refuses more with a 422. */
export const MAX_BULK_SELECTION = 50;

export const RUN_STATUS_LABEL: Record<PipelineBulkRunStatus, string> = {
  pending: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TERMINAL: ReadonlySet<PipelineBulkRunStatus> = new Set([
  "completed",
  "partial",
  "failed",
  "cancelled",
]);

export function isTerminalRun(status: PipelineBulkRunStatus): boolean {
  return TERMINAL.has(status);
}

/** Processed share as a whole percent. A partial run at 100% processed is
 * still "Partial" — the label comes from `status`, never from this number. */
export function progressPercent(run: Pick<PipelineBulkRun, "processedCount" | "totalCount">): number {
  if (run.totalCount <= 0) return 0;
  return Math.min(100, Math.round((run.processedCount / run.totalCount) * 100));
}

/** True while a cancel was accepted but a worker still holds the run (H-2). */
export function isCancelling(run: Pick<PipelineBulkRun, "status" | "cancelRequestedAt">): boolean {
  return run.status === "running" && run.cancelRequestedAt !== null;
}

/**
 * The fingerprint of one merchant intent (plan §19).
 *
 * The idempotency key is minted per snapshot and reused for every retry of
 * that same snapshot. The backend fingerprints sorted unique ids + tone +
 * store, so order of selection does not create a new intent.
 */
export interface BulkStartSnapshot {
  productIds: string[];
  tone: AITone;
  storeId: string | null;
}

export function snapshotOf(ids: Iterable<string>, tone: AITone, storeId: string | null): BulkStartSnapshot {
  return { productIds: [...new Set(ids)].sort(), tone, storeId };
}

export function sameSnapshot(a: BulkStartSnapshot | null, b: BulkStartSnapshot): boolean {
  if (a === null) return false;
  return (
    a.tone === b.tone &&
    a.storeId === b.storeId &&
    a.productIds.length === b.productIds.length &&
    a.productIds.every((id, index) => id === b.productIds[index])
  );
}

/** Session key remembering the run this browser started (plan §23). */
export function activeRunStorageKey(tenantId: string): string {
  return `droppilot.aiStudio.activeRun.${tenantId}`;
}

export function readActiveRun(tenantId: string): string | null {
  try {
    return window.sessionStorage.getItem(activeRunStorageKey(tenantId));
  } catch {
    return null;
  }
}

export function writeActiveRun(tenantId: string, runId: string | null): void {
  try {
    if (runId === null) window.sessionStorage.removeItem(activeRunStorageKey(tenantId));
    else window.sessionStorage.setItem(activeRunStorageKey(tenantId), runId);
  } catch {
    // Storage can be unavailable (private mode, blocked site data). The run is
    // durable on the server; losing the shortcut only loses the deep link.
  }
}
