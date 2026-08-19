"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleSlash,
  Clock,
  Loader2,
  XCircle,
} from "lucide-react";
import { useMemo, useState, type ComponentType } from "react";

import { explainReason } from "@/components/global-rules/impact-row";
import { Callout, Select, trimDecimal } from "@/components/global-rules/rule-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { cn, formatDateTime } from "@/lib/utils";
import {
  TERMINAL_STATUSES,
  useApplication,
  useCancelApplication,
  type Application,
  type ApplicationItem,
  type ApplicationOutcome,
  type ApplicationStatus,
} from "@/services/global-rules";

/**
 * Progress and results for one confirmed application.
 *
 * Polls until the status can no longer change, then stops. Resuming after a
 * browser refresh costs nothing extra: the application id lives in the URL, so
 * reloading re-mounts this against the same run rather than starting another.
 */

const STATUS_COPY: Record<
  ApplicationStatus,
  { label: string; description: string; icon: ComponentType<{ className?: string }> }
> = {
  pending: {
    label: "Queued",
    description: "Accepted and waiting for a worker to pick it up.",
    icon: Clock,
  },
  running: {
    label: "Applying",
    description: "Working through the selection in batches.",
    icon: Loader2,
  },
  completed: {
    label: "Completed",
    description: "Every selected draft was handled.",
    icon: CheckCircle2,
  },
  partial: {
    label: "Partly applied",
    description:
      "Some drafts were repriced and some were not. The ones that were are already saved.",
    icon: AlertTriangle,
  },
  failed: {
    label: "Failed",
    description:
      "The run stopped early. Anything already repriced was kept — see the results below.",
    icon: XCircle,
  },
  cancelled: {
    label: "Cancelled",
    description:
      "Stopped on request. Drafts repriced before it stopped keep their new prices.",
    icon: Ban,
  },
};

const OUTCOME_COPY: Record<ApplicationOutcome, string> = {
  applied: "Applied",
  skipped: "Skipped",
  needs_review: "Needs review",
  stale: "Stale",
  published: "Published",
  failed: "Failed",
};

const OUTCOME_FILTERS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "All outcomes" },
  { value: "applied", label: "Applied" },
  { value: "skipped", label: "Skipped" },
  { value: "needs_review", label: "Needs review" },
  { value: "stale", label: "Stale" },
  { value: "published", label: "Published" },
  { value: "failed", label: "Failed" },
];

const PAGE_SIZE = 25;

function Count({
  label,
  value,
  testId,
}: {
  label: string;
  value: number;
  testId: string;
}) {
  return (
    <div className="rounded-md border p-2 text-center">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd data-testid={testId} className="font-mono text-lg tabular-nums">
        {value}
      </dd>
    </div>
  );
}

function ResultRow({ item }: { item: ApplicationItem }) {
  return (
    <li className="rounded-md border p-3" data-testid="result-row" data-outcome={item.outcome}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={item.outcome === "applied" ? "default" : "outline"}>
            {OUTCOME_COPY[item.outcome]}
          </Badge>
          <span className="font-mono text-xs text-muted-foreground">
            {item.variantId ? "variant" : "product"}{" "}
            {item.productId ? item.productId.slice(0, 8) : "unresolved"}
          </span>
        </div>
        {item.appliedRuleVersion !== null && (
          <span className="text-xs text-muted-foreground">
            rule v{item.appliedRuleVersion}
          </span>
        )}
      </div>

      {(item.previousPrice !== null || item.newPrice !== null) && (
        <p className="mt-1 font-mono text-sm tabular-nums">
          {item.previousPrice === null ? "—" : trimDecimal(item.previousPrice, 2)}
          <span aria-hidden="true"> → </span>
          <span className="sr-only"> changed to </span>
          {item.newPrice === null ? "—" : trimDecimal(item.newPrice, 2)}
        </p>
      )}

      {item.message && <p className="mt-1 text-sm text-muted-foreground">{item.message}</p>}
      {item.reviewReasons.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
          {item.reviewReasons.map((reason) => (
            <li key={reason}>{explainReason(reason)}</li>
          ))}
        </ul>
      )}
      {/* A draft that was repriced is worth opening; one that was skipped for
          being published is not something this workflow can act on. */}
      {item.productId && item.outcome === "applied" && (
        <a
          href={`/drafts/${item.productId}`}
          className="mt-2 inline-flex min-h-10 items-center text-xs font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Open this draft
        </a>
      )}
    </li>
  );
}

export function ApplicationProgress({
  applicationId,
  canManage,
  onDismiss,
}: {
  applicationId: string;
  canManage: boolean;
  onDismiss: () => void;
}) {
  const application = useApplication(applicationId);
  const cancel = useCancelApplication(applicationId);
  const [outcomeFilter, setOutcomeFilter] = useState("");
  const [page, setPage] = useState(1);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const data: Application | undefined = application.data;

  const filtered = useMemo(() => {
    const items = data?.items ?? [];
    return outcomeFilter ? items.filter((item) => item.outcome === outcomeFilter) : items;
  }, [data, outcomeFilter]);

  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));

  if (application.isLoading) {
    return (
      <div className="space-y-3" role="status" aria-live="polite">
        <span className="sr-only">Loading application status</span>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (application.isError || !data) {
    return (
      <ErrorState
        title="Could not load this application"
        description="Its status could not be read. Any prices it already wrote are unaffected."
        requestId={
          application.error instanceof ApiError ? application.error.requestId : null
        }
        onRetry={() => void application.refetch()}
      />
    );
  }

  const status = STATUS_COPY[data.status];
  const StatusIcon = status.icon;
  const terminal = TERMINAL_STATUSES.includes(data.status);
  const percent =
    data.totalCount === 0
      ? 0
      : Math.min(100, Math.round((data.processedCount / data.totalCount) * 100));
  const cancellable = canManage && (data.status === "pending" || data.status === "running");

  async function runCancel() {
    setCancelError(null);
    try {
      await cancel.mutateAsync();
      setConfirmCancel(false);
    } catch (err) {
      setCancelError(
        err instanceof ApiError
          ? err.message
          : "This application could not be cancelled.",
      );
    }
  }

  return (
    <div className="space-y-4" data-testid="application-progress" data-status={data.status}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* The icon is decorative; the label carries the state, so
                progress is never conveyed by colour or shape alone. */}
            <StatusIcon
              className={cn("h-4 w-4", data.status === "running" && "animate-spin")}
              aria-hidden="true"
            />
            <span className="font-medium" data-testid="application-status">
              {status.label}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {applicationId.slice(0, 8)}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">{status.description}</p>
          {data.failureReason && (
            <p className="text-sm text-destructive" role="alert">
              {data.failureReason}
            </p>
          )}
          {data.recoveryCount > 0 && (
            <p className="text-xs text-muted-foreground">
              Recovered {data.recoveryCount} time
              {data.recoveryCount === 1 ? "" : "s"} after a worker restart.
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          {cancellable && (
            <Button
              type="button"
              variant="outline"
              className="min-h-10"
              onClick={() => setConfirmCancel(true)}
              data-testid="cancel-application"
            >
              <CircleSlash className="mr-2 h-4 w-4" aria-hidden="true" />
              Cancel
            </Button>
          )}
          {terminal && (
            <Button type="button" variant="outline" className="min-h-10" onClick={onDismiss}>
              Back to preview
            </Button>
          )}
        </div>
      </div>

      <div>
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label="Application progress"
          className="h-2 w-full overflow-hidden rounded-full bg-muted"
        >
          <div
            className="h-full bg-primary transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
        {/* Announced politely so a screen reader hears progress without the
            updates interrupting whatever else is being read. */}
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs text-muted-foreground"
          data-testid="application-progress-text"
        >
          {data.processedCount} of {data.totalCount} processed ({percent}%)
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Count label="Applied" value={data.appliedCount} testId="count-applied" />
        <Count label="Skipped" value={data.skippedCount} testId="count-skipped" />
        <Count label="Review" value={data.reviewCount} testId="count-review" />
        <Count label="Failed" value={data.failedCount} testId="count-failed" />
        <Count label="Total" value={data.totalCount} testId="count-total" />
      </dl>

      {data.status === "partial" && (
        <Callout tone="warning" title="Some drafts were not repriced">
          <p>
            The successful ones are already saved and are not undone by the
            rest. Filter to <strong>Needs review</strong> or{" "}
            <strong>Failed</strong> below to see what to fix, then run a new
            application for those.
          </p>
        </Callout>
      )}

      <section className="space-y-3" aria-labelledby="results-heading">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 id="results-heading" className="text-sm font-medium">
            Results
          </h3>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Outcome</span>
            <Select
              value={outcomeFilter}
              onChange={(value) => {
                setOutcomeFilter(value);
                setPage(1);
              }}
              options={OUTCOME_FILTERS}
            />
          </label>
        </div>

        {data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {terminal
              ? "This run recorded no items."
              : "Results appear here as each batch completes."}
          </p>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No items with that outcome.
          </p>
        ) : (
          <>
            <ul className="space-y-2">
              {pageItems.map((item, index) => (
                <ResultRow
                  key={`${item.productId ?? "none"}-${item.variantId ?? "none"}-${index}`}
                  item={item}
                />
              ))}
            </ul>
            {totalPages > 1 && (
              <nav
                className="flex flex-wrap items-center justify-between gap-2"
                aria-label="Results pagination"
              >
                <p className="text-xs text-muted-foreground" aria-live="polite">
                  Page {page} of {totalPages} · {filtered.length} items
                </p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="min-h-10"
                    disabled={page <= 1}
                    onClick={() => setPage((current) => current - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="min-h-10"
                    disabled={page >= totalPages}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    Next
                  </Button>
                </div>
              </nav>
            )}
          </>
        )}
      </section>

      {data.finishedAt && (
        <p className="text-xs text-muted-foreground">
          Finished {formatDateTime(data.finishedAt)}.
        </p>
      )}

      <Dialog open={confirmCancel} onOpenChange={(next) => !next && setConfirmCancel(false)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Stop this application?</DialogTitle>
            <DialogDescription>
              {data.status === "pending"
                ? "It has not started, so nothing has been written."
                : "It stops at the end of the current batch. Drafts already repriced keep their new prices — they are real writes and are not undone."}
            </DialogDescription>
          </DialogHeader>
          {cancelError && (
            <p role="alert" className="text-sm text-destructive">
              {cancelError}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="destructive"
              className="min-h-10"
              disabled={cancel.isPending}
              onClick={() => void runCancel()}
              data-testid="confirm-cancel"
            >
              {cancel.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              )}
              Stop it
            </Button>
            <Button
              type="button"
              variant="outline"
              className="min-h-10"
              onClick={() => setConfirmCancel(false)}
            >
              Keep going
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
