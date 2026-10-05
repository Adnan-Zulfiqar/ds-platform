"use client";

import { Badge } from "@/components/ui/badge";
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
import { useInventoryChanges, useInventorySyncRuns } from "@/services/inventory";

function runTone(status: string): "success" | "destructive" | "secondary" {
  if (status === "succeeded" || status === "completed") return "success";
  if (status === "failed") return "destructive";
  return "secondary";
}

/**
 * What the sync did, and what it found. Both lists existed in the API since
 * Phase 6 and had no screen, so "did last night's sync run?" could only be
 * answered from the database.
 */
export function InventoryHistory() {
  const runs = useInventorySyncRuns({ page: 1, size: 10 });
  const changes = useInventoryChanges({ page: 1, size: 20 });

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sync runs</CardTitle>
          <CardDescription>The last 10 runs, scheduled or manual.</CardDescription>
        </CardHeader>
        <CardContent>
          {runs.isError ? (
            <ErrorState title="Could not load sync runs" onRetry={() => void runs.refetch()} />
          ) : runs.isLoading || !runs.data ? (
            <Skeleton className="h-32 w-full" />
          ) : runs.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sync has run yet.</p>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Trigger</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Seen / changed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.items.map((run) => (
                    <TableRow key={run.id} data-testid="inventory-run-row">
                      <TableCell>{formatDateTime(run.startedAt ?? run.createdAt)}</TableCell>
                      <TableCell>{run.trigger}</TableCell>
                      <TableCell>
                        <Badge variant={runTone(run.status)}>{run.status}</Badge>
                        {run.errorMessage && (
                          <span className="ml-2 text-xs text-destructive">{run.errorMessage}</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {run.productsSeen} / {run.productsChanged}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stock changes</CardTitle>
          <CardDescription>The last 20 quantity movements, newest first.</CardDescription>
        </CardHeader>
        <CardContent>
          {changes.isError ? (
            <ErrorState
              title="Could not load stock changes"
              onRetry={() => void changes.refetch()}
            />
          ) : changes.isLoading || !changes.data ? (
            <Skeleton className="h-32 w-full" />
          ) : changes.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No stock has changed yet.</p>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Quantity</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {changes.data.items.map((change) => (
                    <TableRow key={change.id} data-testid="inventory-change-row">
                      <TableCell>{formatDateTime(change.createdAt)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {change.productId.slice(0, 8)}
                      </TableCell>
                      <TableCell>
                        {change.previousQuantity} → {change.newQuantity}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {change.reason}
                        {change.note ? ` · ${change.note}` : ""}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
