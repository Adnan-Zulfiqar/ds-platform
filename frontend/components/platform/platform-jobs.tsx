"use client";

import Link from "next/link";
import { useState } from "react";

import { Pager } from "@/components/platform/platform-workspaces";
import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { describeError } from "@/components/platform/platform-workspace-actions";
import { useReauthGuard } from "@/components/platform/reauth-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn, formatDateTime } from "@/lib/utils";
import {
  type JobKind,
  type PlatformJob,
  useJobAction,
  usePlatformJobs,
  usePlatformJobsSummary,
} from "@/services/platform";

/**
 * Admin Control Center phase 7 (D-019): failed and stuck background work
 * across every workspace. Celery keeps no job table, so these are the run
 * tables the application writes. An action on a job happens in the job's
 * workspace and needs a support session there.
 */

const KINDS: { kind: JobKind; label: string; action: string | null }[] = [
  { kind: "order_sync", label: "Order syncs", action: "Close stuck run" },
  {
    kind: "inventory_sync",
    label: "Inventory syncs",
    action: "Close stuck run",
  },
  { kind: "product_import", label: "Product imports", action: null },
  { kind: "pipeline_run", label: "AI pipeline runs", action: "Cancel" },
  { kind: "rule_application", label: "Pricing rule runs", action: "Cancel" },
  { kind: "supplier_order", label: "Supplier orders", action: null },
  { kind: "automation_run", label: "Automation runs", action: "Run again" },
];

export function PlatformJobs() {
  return (
    <RequirePermission permission="jobs.read">
      <PlatformPageHeader
        title="Jobs"
        description="Failed in the last 7 days, or stuck now, across every workspace."
      />
      <JobsNotice />
      <Jobs />
    </RequirePermission>
  );
}

function Jobs() {
  const [kind, setKind] = useState<JobKind>("order_sync");
  const [state, setState] = useState<"failed" | "stuck">("failed");
  const [page, setPage] = useState(1);
  const summary = usePlatformJobsSummary();
  const jobs = usePlatformJobs(kind, state, page);

  return (
    <>
      <div
        className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7"
        data-testid="platform-jobs-summary"
      >
        {KINDS.map((k) => {
          const counts = summary.data?.[k.kind];
          return (
            <button
              key={k.kind}
              type="button"
              aria-pressed={kind === k.kind}
              onClick={() => {
                setKind(k.kind);
                setPage(1);
              }}
              className={cn(
                "rounded-lg border p-3 text-left text-sm",
                kind === k.kind
                  ? "border-primary bg-primary/5"
                  : "hover:bg-accent/40",
              )}
            >
              <div className="font-medium">{k.label}</div>
              <div className="text-muted-foreground">
                {counts
                  ? `${counts.failed} failed · ${counts.stuck} stuck`
                  : "…"}
              </div>
            </button>
          );
        })}
      </div>
      <div className="flex gap-2" role="group" aria-label="Job state">
        {(["failed", "stuck"] as const).map((s) => (
          <Button
            key={s}
            size="sm"
            variant={state === s ? "default" : "outline"}
            aria-pressed={state === s}
            onClick={() => {
              setState(s);
              setPage(1);
            }}
          >
            {s === "failed" ? "Failed" : "Stuck"}
          </Button>
        ))}
      </div>
      {jobs.isError ? (
        <ErrorState
          title="Could not load jobs"
          onRetry={() => void jobs.refetch()}
        />
      ) : !jobs.data ? (
        <Skeleton className="h-48 w-full" />
      ) : jobs.data.items.length === 0 ? (
        <EmptyState
          title="Nothing here"
          description={
            state === "failed"
              ? "No failures in the last 7 days."
              : "Nothing is stuck."
          }
        />
      ) : (
        <>
          <Card>
            <CardContent className="overflow-x-auto p-0">
              <Table data-testid="platform-jobs">
                <TableHeader>
                  <TableRow>
                    <TableHead>Workspace</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Error</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {jobs.data.items.map((job) => (
                    <JobRow key={job.id} job={job} state={state} />
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Pager
            page={page}
            totalPages={jobs.data.meta.totalPages}
            hasNext={jobs.data.meta.hasNext}
            onPage={setPage}
          />
        </>
      )}
    </>
  );
}

function JobRow({
  job,
  state,
}: {
  job: PlatformJob;
  state: "failed" | "stuck";
}) {
  const { can } = usePlatformAccess();
  const act = useJobAction();
  const guard = useReauthGuard();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const spec = KINDS.find((k) => k.kind === job.kind);
  // Closing is only for a sync that is stuck; cancel and retry follow the
  // merchant's own rules, which the server enforces either way.
  const offered =
    can("jobs.manage") &&
    spec?.action != null &&
    !(
      state === "failed" &&
      (job.kind === "order_sync" || job.kind === "inventory_sync")
    ) &&
    !(
      state === "failed" &&
      (job.kind === "pipeline_run" || job.kind === "rule_application")
    );

  return (
    <TableRow>
      <TableCell>
        <Link
          className="font-medium underline-offset-2 hover:underline"
          href={`/platform/workspaces/${job.tenantId}`}
        >
          {job.tenantName}
        </Link>
      </TableCell>
      <TableCell>
        <Badge variant="destructive">{job.status}</Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">
        {job.startedAt
          ? formatDateTime(job.startedAt)
          : formatDateTime(job.createdAt)}
      </TableCell>
      <TableCell className="max-w-[28rem]">
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {job.error ?? "—"}
        </span>
        {error && (
          <span className="block text-xs text-destructive">{error}</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        {done ? (
          <span className="text-xs text-muted-foreground">Done</span>
        ) : offered ? (
          <Button
            size="sm"
            variant="outline"
            disabled={act.isPending}
            onClick={() => {
              const reason =
                window.prompt(
                  `Reason to ${spec?.action?.toLowerCase()} (audited):`,
                ) ?? "";
              if (reason.trim().length < 3) return;
              setError(null);
              guard(() => act.mutateAsync({ job, reason: reason.trim() })).then(
                () => setDone(true),
                (e: unknown) => setError(describeError(e)),
              );
            }}
          >
            {spec?.action}
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function JobsNotice() {
  return (
    <Alert>
      <AlertDescription>
        Celery task failures that never wrote a run row appear in the worker
        logs only.
      </AlertDescription>
    </Alert>
  );
}
