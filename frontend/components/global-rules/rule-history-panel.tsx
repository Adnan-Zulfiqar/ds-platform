"use client";

import { History, Loader2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/global-rules/rule-primitives";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import { useRuleHistory, type RuleKind, type RuleVersion } from "@/services/global-rules";

/**
 * Append-only version history for one rule.
 *
 * Deliberately read-only: there are no edit or delete controls, and none
 * should be added. A trail that can be changed is not a trail — the whole
 * value of these rows is that they record what was true, including for rules
 * that were later deactivated or deleted.
 */

const PAGE_SIZE = 10;

/** Snapshot values arrive as JSON scalars. Render them without inventing types. */
function display(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** `markup_percent` → `Markup percent`. The API records column names. */
function humanise(field: string): string {
  const spaced = field.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function VersionEntry({ version }: { version: RuleVersion }) {
  const [open, setOpen] = useState(false);
  const changes = version.changedFields;

  return (
    <li className="rounded-lg border p-4" data-testid="history-entry">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">Version {version.version}</Badge>
            <Badge variant="outline" className="capitalize">
              {version.ruleKind}
            </Badge>
            <StatusBadge active={version.isActive} />
          </div>
          <p className="text-sm text-muted-foreground">
            {changes.length === 0
              ? "Created."
              : `${changes.length} field${changes.length === 1 ? "" : "s"} changed.`}
          </p>
          {version.note && (
            <p className="text-sm">
              <span className="text-muted-foreground">Note: </span>
              {version.note}
            </p>
          )}
        </div>
        <div className="text-xs text-muted-foreground sm:text-right">
          <p>{formatDateTime(version.createdAt)}</p>
          <p className="break-all">
            {version.changedByUserId ? `By ${version.changedByUserId}` : "By the system"}
          </p>
        </div>
      </div>

      {changes.length > 0 && (
        <div className="mt-3">
          {/* A native disclosure: keyboard operable and announced as expanded
              or collapsed without any JavaScript to get wrong. */}
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            className="min-h-10 rounded text-sm font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {open ? "Hide change details" : "Show change details"}
          </button>
          {open && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[26rem] text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th scope="col" className="py-1 pr-4 font-medium">
                      Field
                    </th>
                    <th scope="col" className="py-1 pr-4 font-medium">
                      Previous
                    </th>
                    <th scope="col" className="py-1 font-medium">
                      New
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((field) => (
                    <tr key={field} className="border-t">
                      <th scope="row" className="py-1 pr-4 font-normal">
                        {humanise(field)}
                      </th>
                      <td className="py-1 pr-4 font-mono text-muted-foreground">
                        {display(version.previousValues[field])}
                      </td>
                      <td className="py-1 font-mono">
                        {display(version.newValues[field])}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function RuleHistoryPanel({
  kind,
  ruleId,
  ruleName,
}: {
  kind: RuleKind;
  ruleId: string;
  ruleName: string;
}) {
  const [page, setPage] = useState(1);
  const history = useRuleHistory(kind, ruleId, page, PAGE_SIZE);

  if (history.isLoading) {
    return (
      <div className="space-y-3" role="status" aria-live="polite">
        <span className="sr-only">Loading rule history</span>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (history.isError) {
    return (
      <ErrorState
        title="Could not load history"
        description="The rule's version history could not be read."
        requestId={
          history.error instanceof ApiError ? history.error.requestId : null
        }
        onRetry={() => void history.refetch()}
      />
    );
  }

  const versions = history.data?.items ?? [];
  const meta = history.data?.meta;

  if (versions.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No history yet"
        description="Every change to this rule will be recorded here."
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Every change to <span className="font-medium text-foreground">{ruleName}</span>,
        newest first. These records cannot be edited or removed.
      </p>

      <ul className="space-y-3">
        {versions.map((version) => (
          <VersionEntry key={version.id} version={version} />
        ))}
      </ul>

      {meta && meta.totalPages > 1 && (
        <nav
          className="flex flex-wrap items-center justify-between gap-2"
          aria-label="History pagination"
        >
          <p className="text-xs text-muted-foreground" aria-live="polite">
            Page {meta.page} of {meta.totalPages} · {meta.totalItems} versions
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="min-h-10"
              disabled={!meta.hasPrevious || history.isFetching}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="min-h-10"
              disabled={!meta.hasNext || history.isFetching}
              onClick={() => setPage((value) => value + 1)}
            >
              Next
            </Button>
            {history.isFetching && (
              <Loader2
                className="h-4 w-4 animate-spin text-muted-foreground"
                aria-hidden="true"
              />
            )}
          </div>
        </nav>
      )}
    </div>
  );
}
