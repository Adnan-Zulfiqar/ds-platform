"use client";

import { History } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
import { useProductImports } from "@/services/products";
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

/**
 * Persistent import job history — successes and failures.
 *
 * Failures are the point: "why is this product missing" is asked long after
 * the attempt ran.
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
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
