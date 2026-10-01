import type { PipelinePreview } from "@/types/api";

/**
 * The two meanings of `approvalExpectedUpdatedAt` (PHASE_9_STAGE_10_PLAN.md
 * §11).
 *
 * The server always fills it with `Product.updatedAt` at compose time. What
 * it may be used for depends on `candidateActive`:
 *
 * - inactive candidate → it is the **approval token (T0)**. Approve only.
 * - active candidate   → it is the **current publish token**. Publish only.
 *
 * Publishing with an inactive candidate's value would send a token the
 * approval is about to move, so these helpers are the only place either
 * token is read from a preview.
 */

export function approvalTokenOf(preview: PipelinePreview): string | null {
  return preview.candidateActive ? null : preview.approvalExpectedUpdatedAt;
}

export function publishTokenOf(preview: PipelinePreview): string | null {
  return preview.candidateActive ? preview.approvalExpectedUpdatedAt : null;
}

/**
 * Choose the publish token after an approve or a refreshed GET.
 *
 * The approve response's `updatedAt` (T1) is adopted first. A later GET of
 * the same, now-active candidate may carry a newer `approvalExpectedUpdatedAt`
 * (T2) if the product moved again; publishing needs the newest server token,
 * so a later value wins. ISO timestamps from the same server compare by
 * instant, not by string.
 */
export function newerToken(current: string | null, candidate: string | null): string | null {
  if (current === null) return candidate;
  if (candidate === null) return current;
  return Date.parse(candidate) > Date.parse(current) ? candidate : current;
}

/** True when the server already knows this inactive candidate is stale. */
export function hasStalePreviewBlocker(preview: PipelinePreview): boolean {
  return (
    !preview.candidateActive &&
    preview.pipelineBlockers.some((blocker) => blocker.code === "stale_preview")
  );
}

/** Synthetic candidates come from the test provider and can never publish. */
export function isTestPreview(preview: PipelinePreview): boolean {
  return preview.isSynthetic || preview.provider === "stub";
}
