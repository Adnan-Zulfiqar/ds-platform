"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDateTime } from "@/lib/utils";
import { usePriceChanges } from "@/services/pricing";

/** The audit trail the Pricing page always promised ("every change is
 * audited") but never showed. Read-only; the API had it since Phase 6. */
export function PriceChangeLog() {
  const { data, isLoading, isError, refetch } = usePriceChanges({ page: 1, size: 20 });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent price changes</CardTitle>
        <CardDescription>The last 20 prices the rules applied, newest first.</CardDescription>
      </CardHeader>
      <CardContent>
        {isError ? (
          <ErrorState title="Could not load price changes" onRetry={() => void refetch()} />
        ) : isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No prices have been changed yet.</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Price</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((change) => (
                  <TableRow key={change.id} data-testid="price-change-row">
                    <TableCell>{formatDateTime(change.appliedAt)}</TableCell>
                    <TableCell className="font-mono text-xs">{change.productId.slice(0, 8)}</TableCell>
                    <TableCell>
                      {change.previousPrice ?? "—"} → {change.newPrice ?? "—"}
                      {change.currency ? ` ${change.currency}` : ""}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{change.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
