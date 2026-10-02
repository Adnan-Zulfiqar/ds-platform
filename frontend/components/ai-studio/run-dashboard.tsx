"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  isCancelling,
  isTerminalRun,
  progressPercent,
  RUN_STATUS_LABEL,
  writeActiveRun,
} from "@/lib/ai-studio/bulk";
import { requestIdOf, studioErrorMessage } from "@/lib/ai-studio/errors";
import { useAuth } from "@/providers/auth-provider";
import { draftKeys } from "@/services/drafts";
import {
  productKeys,
  useCancelPipelineRun,
  usePipelineRun,
  usePipelineRunItems,
} from "@/services/products";
import type { Page, PipelineBulkRunItem, Product } from "@/types/api";

/**
 * Names for run items, from the Drafts and Products pages already in the
 * query cache (the merchant selected from them). An item whose page is not
 * cached shows its id — the run itself never carries titles.
 */
function useCachedProductTitles(): ReadonlyMap<string, string> {
  const queryClient = useQueryClient();
  const titles = new Map<string, string>();
  for (const key of [draftKeys.lists(), productKeys.lists()]) {
    for (const [, page] of queryClient.getQueriesData<Page<Product>>({ queryKey: key })) {
      for (const product of page?.items ?? []) titles.set(product.id, product.title);
    }
  }
  return titles;
}

const ITEMS_PAGE_SIZE = 20;

/**
 * A bulk run's durable progress (plan §20–§22).
 *
 * Polls the run every 5 s while it is pending or running and stops on a
 * terminal status. Counts and the status word come from the server; the
 * client never marks leftover items itself — after a cancel, rows still
 * `pending` stay `pending`, and the run `status` is the authority.
 */
export function RunDashboard({ runId }: { runId: string }) {
  const titles = useCachedProductTitles();
  const { identity } = useAuth();
  const tenantId = identity?.tenant.id ?? null;
  const run = usePipelineRun(runId);
  const [page, setPage] = useState(1);
  const live = run.data !== undefined && !isTerminalRun(run.data.status);
  const items = usePipelineRunItems(runId, { page, size: ITEMS_PAGE_SIZE }, { refetchInterval: live ? 5_000 : false });
  const cancel = useCancelPipelineRun(runId);
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    // Forget the browser's shortcut once the run is over (plan §23).
    if (tenantId && run.data && isTerminalRun(run.data.status)) writeActiveRun(tenantId, null);
  }, [run.data, tenantId]);

  if (run.isPending) return <Skeleton className="h-48 w-full" data-testid="ai-studio-run-loading" />;
  if (run.isError) {
    const notFound = run.error instanceof ApiError && run.error.code === "not_found";
    return (
      <ErrorState
        title={notFound ? "Run not found" : "Could not load this run"}
        description={notFound ? "This bulk run does not exist in this workspace." : studioErrorMessage(run.error)}
        requestId={requestIdOf(run.error)}
        onRetry={notFound ? undefined : () => void run.refetch()}
      />
    );
  }

  const summary = run.data;
  const cancelling = isCancelling(summary);
  const canCancel = (summary.status === "pending" || summary.status === "running") && !cancelling;

  return (
    <section className="space-y-4" aria-labelledby="ai-studio-run-heading" data-testid="ai-studio-run">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="ai-studio-run-heading" className="text-lg font-semibold">
          Bulk run
        </h2>
        {canCancel ? (
          <Button type="button" variant="outline" onClick={() => setConfirmCancel(true)} data-testid="ai-studio-run-cancel">
            Cancel run
          </Button>
        ) : null}
      </div>

      <div aria-live="polite" className="rounded-lg border p-4 text-sm" data-testid="ai-studio-run-summary">
        <p className="text-base font-semibold" data-testid="ai-studio-run-status" data-status={summary.status}>
          {cancelling ? "Cancelling…" : RUN_STATUS_LABEL[summary.status]}
        </p>
        <p>
          {summary.processedCount} of {summary.totalCount} processed ({progressPercent(summary)}%) ·{" "}
          {summary.succeededCount} succeeded · {summary.failedCount} failed · {summary.skippedCount} skipped ·{" "}
          {summary.missingCount} missing
        </p>
        {summary.failureReason ? (
          <p className="mt-1 text-destructive" data-testid="ai-studio-run-failure">
            {summary.failureReason}
          </p>
        ) : null}
        {cancelling ? (
          <p className="mt-1 text-muted-foreground">
            Cancellation requested. The product already being optimized may still finish.
          </p>
        ) : null}
        {summary.status === "cancelled" ? (
          <p className="mt-1 text-muted-foreground">
            This run was cancelled. Items still listed as pending were not processed.
          </p>
        ) : null}
      </div>

      {cancel.isError ? (
        <Alert variant="destructive" data-testid="ai-studio-run-cancel-error">
          <AlertDescription>
            {cancel.error instanceof ApiError && cancel.error.code === "conflict"
              ? "This run has already finished, so it cannot be cancelled."
              : studioErrorMessage(cancel.error)}
          </AlertDescription>
        </Alert>
      ) : null}

      <RunItems
        items={items.data?.items ?? []}
        loading={items.isPending}
        titles={titles}
        page={page}
        hasNext={items.data?.meta.hasNext ?? false}
        onPage={setPage}
      />

      <Dialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <DialogContent data-testid="ai-studio-run-cancel-dialog">
          <DialogHeader>
            <DialogTitle>Stop this run?</DialogTitle>
            <DialogDescription>
              The product already being optimized may still finish. Cancellation is not instant.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmCancel(false)}>
              Keep running
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(undefined, { onSettled: () => setConfirmCancel(false) })}
              data-testid="ai-studio-run-cancel-confirm"
            >
              {cancel.isPending ? "Cancelling…" : "Cancel run"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

const STATE_LABEL: Record<PipelineBulkRunItem["state"], string> = {
  pending: "Pending",
  succeeded: "Preview ready",
  failed: "Failed",
  skipped: "Skipped",
  missing: "Missing",
};

function RunItems({
  items,
  loading,
  titles,
  page,
  hasNext,
  onPage,
}: {
  items: PipelineBulkRunItem[];
  loading: boolean;
  titles: ReadonlyMap<string, string>;
  page: number;
  hasNext: boolean;
  onPage: (page: number) => void;
}) {
  if (loading) return <Skeleton className="h-32 w-full" />;
  return (
    <div className="space-y-3">
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No items on this page</p>
      ) : (
        <ul className="divide-y rounded-lg border" data-testid="ai-studio-run-items">
          {items.map((item) => {
            // Only a succeeded item with both ids has a candidate to open; a
            // missing item may have no product at all, and no link is invented.
            const reviewable =
              item.state === "succeeded" && item.productId !== null && item.candidateVersionId !== null;
            return (
              <li
                key={item.submittedProductId}
                className="flex flex-col gap-2 p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                data-testid="ai-studio-run-item"
                data-state={item.state}
              >
                <div className="min-w-0 space-y-0.5">
                  <p className="truncate font-medium">{titles.get(item.submittedProductId) ?? item.submittedProductId}</p>
                  <p className="text-xs text-muted-foreground">
                    <Badge variant={item.state === "failed" ? "destructive" : "outline"} className="mr-2">
                      {STATE_LABEL[item.state] ?? item.state}
                    </Badge>
                    Attempts: {item.attemptCount}
                    {item.errorCode ? ` · ${item.errorCode}` : ""}
                    {item.errorMessage ? ` · ${item.errorMessage}` : ""}
                  </p>
                </div>
                {reviewable ? (
                  <Button asChild size="sm" variant="outline">
                    <Link
                      href={`/ai-studio/products/${item.productId}?candidate=${item.candidateVersionId}`}
                      data-testid="ai-studio-run-item-review"
                    >
                      Open review
                    </Link>
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={!hasNext} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}
