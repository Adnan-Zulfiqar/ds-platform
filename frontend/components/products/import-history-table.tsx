"use client";

import { useState } from "react";
import Link from "next/link";
import { History, Loader2, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { ApiError } from "@/lib/api-client";
import { useProductImports, useRetryImport } from "@/services/products";
import type { ProductImportRecord } from "@/types/api";

const STATUS_VARIANT: Record<
  ProductImportRecord["status"],
  "default" | "secondary" | "outline" | "destructive"
> = {
  succeeded: "default",
  pending: "secondary",
  running: "secondary",
  skipped: "outline",
  failed: "destructive",
};

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

/** One row's retry state — kept per-row so retrying one failure does not
 * disable the button on every other row while it runs. */
function RetryCell({ row }: { row: ProductImportRecord }) {
  const retryImport = useRetryImport();
  const [retriedDraftId, setRetriedDraftId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

  if (row.status !== "failed") return <TableCell />;

  if (retriedDraftId) {
    return (
      <TableCell>
        <Link
          href={`/drafts/${retriedDraftId}`}
          className="text-sm underline underline-offset-2"
          data-testid="retry-success-link"
        >
          View draft
        </Link>
      </TableCell>
    );
  }

  return (
    <TableCell>
      <div className="flex flex-col items-start gap-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="retry-import-button"
          disabled={retryImport.isPending}
          onClick={() => {
            setRetryError(null);
            retryImport.mutate(row.id, {
              onSuccess: (product) => setRetriedDraftId(product.id),
              onError: (error) => {
                setRetryError(
                  error instanceof ApiError
                    ? error.message
                    : "Retry failed. Try again.",
                );
              },
            });
          }}
        >
          {retryImport.isPending ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          )}
          Retry
        </Button>
        {retryError ? (
          <p className="text-xs text-destructive" role="alert">
            {retryError}
          </p>
        ) : null}
      </div>
    </TableCell>
  );
}

/**
 * Persistent import job history — successes and failures.
 *
 * Failures are the point: "why is this product missing" is asked long after
 * the attempt ran. A failed row stays retryable here without the merchant
 * re-entering the product id/URL and destination from memory (DSers-parity
 * M1 — `docs/dsers-parity/M1_IMPORT_TO_DRAFTS.md`).
 */
export function ImportHistoryTable() {
  const { data, isPending, isError, error, refetch } = useProductImports({
    size: 50,
    sortBy: "created_at",
    sortDir: "desc",
  });

  if (isPending) {
    return (
      <div className="space-y-2" data-testid="imports-loading">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load import history"
        description={
          error instanceof Error ? error.message : "Please try again."
        }
        onRetry={() => void refetch()}
      />
    );
  }

  if (data.items.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No imports yet"
        description="Import as Draft from the Drafts page. Every attempt — success or failure — appears here."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Supplier ID</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Started</TableHead>
            <TableHead>Finished</TableHead>
            <TableHead>Error</TableHead>
            <TableHead>Retry</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((row) => (
            <TableRow key={row.id} data-testid="import-row">
              <TableCell className="font-medium tabular-nums">
                {row.externalId}
              </TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatWhen(row.createdAt)}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatWhen(row.finishedAt)}
              </TableCell>
              <TableCell className="max-w-sm truncate text-muted-foreground">
                {row.errorMessage ?? row.errorCode ?? "—"}
              </TableCell>
              <RetryCell row={row} />
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
