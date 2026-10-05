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
import { useAutomationRules, useAutomationRuns } from "@/services/automation";

function tone(status: string): "success" | "destructive" | "secondary" {
  if (status === "succeeded" || status === "completed") return "success";
  if (status === "failed") return "destructive";
  return "secondary";
}

/** Run history for the rules above. The list existed in the API since
 * Phase 6; the page only ever showed the last-run time per rule. */
export function AutomationRunLog() {
  const runs = useAutomationRuns({ page: 1, size: 20 });
  const rules = useAutomationRules({ page: 1, size: 50 });
  const names = new Map((rules.data?.items ?? []).map((rule) => [rule.id, rule.name]));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent runs</CardTitle>
        <CardDescription>The last 20 runs across every rule, newest first.</CardDescription>
      </CardHeader>
      <CardContent>
        {runs.isError ? (
          <ErrorState title="Could not load runs" onRetry={() => void runs.refetch()} />
        ) : runs.isLoading || !runs.data ? (
          <Skeleton className="h-32 w-full" />
        ) : runs.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No rule has run yet.</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Rule</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Outcome</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data.items.map((run) => (
                  <TableRow key={run.id} data-testid="automation-run-row">
                    <TableCell>{formatDateTime(run.startedAt ?? run.createdAt)}</TableCell>
                    <TableCell>{names.get(run.ruleId) ?? run.ruleId.slice(0, 8)}</TableCell>
                    <TableCell>{run.trigger}</TableCell>
                    <TableCell>
                      <Badge variant={tone(run.status)}>{run.status}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {run.errorMessage ?? run.summary ?? "—"}
                    </TableCell>
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
