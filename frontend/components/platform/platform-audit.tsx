"use client";

import {
  PlatformPageHeader,
  RequirePermission,
} from "@/components/platform/platform-shell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/utils";
import { roleLabel, usePlatformAudit } from "@/services/platform";

export function PlatformAudit() {
  return (
    <RequirePermission permission="audit.read">
      <PlatformPageHeader
        title="Audit log"
        description="Every operator action, newest first."
      />
      <Audit />
    </RequirePermission>
  );
}

function Audit() {
  const audit = usePlatformAudit(true);
  if (audit.isError) {
    return (
      <ErrorState
        title="Could not load the audit log"
        onRetry={() => void audit.refetch()}
      />
    );
  }
  if (!audit.data) return <Skeleton className="h-64 w-full" />;
  if (audit.data.length === 0) {
    return (
      <EmptyState
        title="No operator actions yet"
        description="Every action appears here."
      />
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Operator actions</CardTitle>
        <CardDescription>
          The latest 100, newest first. Rows cannot be edited or deleted.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table
          className="w-full text-left text-sm"
          data-testid="platform-audit"
        >
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-normal">When</th>
              <th className="py-1 pr-3 font-normal">Action</th>
              <th className="py-1 pr-3 font-normal">Outcome</th>
              <th className="py-1 pr-3 font-normal">Role</th>
              <th className="py-1 pr-3 font-normal">Target</th>
              <th className="py-1 pr-3 font-normal">Address</th>
              <th className="py-1 font-normal">Reason</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {audit.data.map((entry) => (
              <tr key={entry.id}>
                <td className="whitespace-nowrap py-1 pr-3 text-muted-foreground">
                  {formatDateTime(entry.createdAt)}
                </td>
                <td className="py-1 pr-3 font-medium">{entry.action}</td>
                <td className="py-1 pr-3">
                  <Badge
                    variant={
                      entry.outcome === "success" ? "outline" : "destructive"
                    }
                  >
                    {entry.outcome}
                  </Badge>
                </td>
                <td className="py-1 pr-3">
                  {entry.actorRole ? roleLabel(entry.actorRole) : "—"}
                </td>
                <td className="py-1 pr-3 text-muted-foreground">
                  {entry.targetType
                    ? `${entry.targetType} ${entry.targetId?.slice(0, 8) ?? ""}`
                    : "—"}
                </td>
                <td className="py-1 pr-3 text-muted-foreground">
                  {entry.clientIp ?? "—"}
                </td>
                <td className="max-w-[16rem] truncate py-1 text-muted-foreground">
                  {typeof entry.detail.reason === "string"
                    ? entry.detail.reason
                    : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
