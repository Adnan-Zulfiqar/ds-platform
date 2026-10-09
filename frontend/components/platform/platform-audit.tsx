"use client";

import { useState, type FormEvent } from "react";

import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { describeError } from "@/components/platform/platform-workspace-actions";
import { Pager } from "@/components/platform/platform-workspaces";
import { useReauthGuard } from "@/components/platform/reauth-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/utils";
import {
  type AuditEntry,
  type AuditFilters,
  downloadAuditExport,
  roleLabel,
  useAuditSearch,
  useSecuritySummary,
} from "@/services/platform";

/**
 * Admin Control Center phase 9 (D-019): the audit and security centre. The
 * trail it reads cannot be changed or removed, even in the database (a
 * trigger refuses UPDATE, DELETE and TRUNCATE).
 */
export function PlatformAudit() {
  return (
    <RequirePermission permission="audit.read">
      <PlatformPageHeader
        title="Audit and security"
        description="Every operator action and refusal, newest first. Rows cannot be edited or deleted."
      />
      <Security />
      <AuditLog />
    </RequirePermission>
  );
}

const ACTION_LABELS: Record<string, string> = {
  login_failed: "Failed sign-ins",
  reauth_failed: "Failed re-authentications",
  permission_denied: "Refused for missing permission",
};

function Security() {
  const [hours, setHours] = useState(24);
  const summary = useSecuritySummary(hours, true);
  return (
    <Card data-testid="platform-security">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">Security</CardTitle>
          <CardDescription>Refusals in the last {hours} hours.</CardDescription>
        </div>
        <select
          aria-label="Security window"
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        >
          <option value={24}>24 hours</option>
          <option value={168}>7 days</option>
          <option value={720}>30 days</option>
        </select>
      </CardHeader>
      <CardContent>
        {summary.isError ? (
          <ErrorState
            title="Could not load"
            onRetry={() => void summary.refetch()}
          />
        ) : !summary.data ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
              {Object.keys(summary.data.byAction).length === 0 && (
                <dt className="text-muted-foreground">No refusals.</dt>
              )}
              {Object.entries(summary.data.byAction).map(([action, n]) => (
                <div key={action} className="contents">
                  <dt className="text-muted-foreground">
                    {ACTION_LABELS[action] ?? action}
                  </dt>
                  <dd className="text-right tabular-nums">{n}</dd>
                </div>
              ))}
            </dl>
            <div className="text-sm">
              <p className="mb-1 font-medium">Most refusals by address</p>
              {summary.data.topIps.length === 0 ? (
                <p className="text-muted-foreground">None.</p>
              ) : (
                <ul className="space-y-0.5 text-muted-foreground">
                  {summary.data.topIps.map((ip) => (
                    <li key={ip.client_ip}>
                      {ip.client_ip}: {ip.failures}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AuditLog() {
  const { can } = usePlatformAccess();
  const guard = useReauthGuard();
  const [draft, setDraft] = useState<AuditFilters>({});
  const [filters, setFilters] = useState<AuditFilters>({});
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<AuditEntry | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const audit = useAuditSearch(filters, page, true);

  function apply(event: FormEvent) {
    event.preventDefault();
    setPage(1);
    setFilters({
      ...draft,
      since: draft.since ? new Date(draft.since).toISOString() : undefined,
      until: draft.until ? new Date(draft.until).toISOString() : undefined,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Operator actions</CardTitle>
        <CardDescription>
          Filter by action (end with _ for a prefix, e.g. workspace_), outcome,
          workspace and time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <form
          className="grid gap-2 md:grid-cols-6"
          onSubmit={apply}
          role="search"
        >
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="audit-action">Action</Label>
            <Input
              id="audit-action"
              value={draft.action ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  action: e.target.value.trim() || undefined,
                })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-outcome">Outcome</Label>
            <select
              id="audit-outcome"
              className="h-9 w-full rounded-md border bg-background px-2 text-sm"
              value={draft.outcome ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  outcome: (e.target.value ||
                    undefined) as AuditFilters["outcome"],
                })
              }
            >
              <option value="">All</option>
              <option value="success">success</option>
              <option value="failure">failure</option>
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-tenant">Workspace id</Label>
            <Input
              id="audit-tenant"
              value={draft.tenantId ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  tenantId: e.target.value.trim() || undefined,
                })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-since">From</Label>
            <Input
              id="audit-since"
              type="date"
              value={draft.since ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, since: e.target.value || undefined })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-until">Until</Label>
            <Input
              id="audit-until"
              type="date"
              value={draft.until ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, until: e.target.value || undefined })
              }
            />
          </div>
          <div className="flex gap-2 md:col-span-6">
            <Button type="submit" variant="outline">
              Apply filters
            </Button>
            {can("audit.export") && (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setExportError(null);
                  guard(() => downloadAuditExport(filters)).catch(
                    (e: unknown) => setExportError(describeError(e)),
                  );
                }}
              >
                Export CSV
              </Button>
            )}
          </div>
        </form>
        {exportError && (
          <Alert variant="destructive">
            <AlertDescription>{exportError}</AlertDescription>
          </Alert>
        )}
        {audit.isError ? (
          <ErrorState
            title="Could not load the audit log"
            onRetry={() => void audit.refetch()}
          />
        ) : !audit.data ? (
          <Skeleton className="h-64 w-full" />
        ) : audit.data.items.length === 0 ? (
          <EmptyState
            title="Nothing matches"
            description="Change or clear the filters."
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table
                className="w-full text-left text-sm"
                data-testid="platform-audit"
              >
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-normal">When</th>
                    <th className="py-1 pr-3 font-normal">Operator</th>
                    <th className="py-1 pr-3 font-normal">Action</th>
                    <th className="py-1 pr-3 font-normal">Outcome</th>
                    <th className="py-1 pr-3 font-normal">Target</th>
                    <th className="py-1 font-normal">Address</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {audit.data.items.map((entry) => (
                    <tr
                      key={entry.id}
                      className="cursor-pointer hover:bg-accent/40"
                      tabIndex={0}
                      onClick={() => setOpen(entry)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") setOpen(entry);
                      }}
                    >
                      <td className="whitespace-nowrap py-1 pr-3 text-muted-foreground">
                        {formatDateTime(entry.createdAt)}
                      </td>
                      <td className="py-1 pr-3">
                        {entry.adminEmail ?? "—"}
                        {entry.actorRole && (
                          <span className="text-xs text-muted-foreground">
                            {" "}
                            ({roleLabel(entry.actorRole)})
                          </span>
                        )}
                      </td>
                      <td className="py-1 pr-3 font-medium">{entry.action}</td>
                      <td className="py-1 pr-3">
                        <Badge
                          variant={
                            entry.outcome === "success"
                              ? "outline"
                              : "destructive"
                          }
                        >
                          {entry.outcome}
                        </Badge>
                      </td>
                      <td className="py-1 pr-3 text-muted-foreground">
                        {entry.targetType
                          ? `${entry.targetType} ${entry.targetId?.slice(0, 8) ?? ""}`
                          : "—"}
                      </td>
                      <td className="py-1 text-muted-foreground">
                        {entry.clientIp ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between">
              <Pager
                page={page}
                totalPages={audit.data.meta.totalPages}
                hasNext={audit.data.meta.hasNext}
                onPage={setPage}
              />
              <span className="text-xs text-muted-foreground">
                {audit.data.meta.totalItems} in total
              </span>
            </div>
          </>
        )}
      </CardContent>
      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent
          side="right"
          className="w-full overflow-y-auto sm:max-w-lg"
        >
          <SheetHeader>
            <SheetTitle>{open?.action}</SheetTitle>
            <SheetDescription>
              {open ? formatDateTime(open.createdAt) : ""}
            </SheetDescription>
          </SheetHeader>
          {open && (
            <dl
              className="mt-4 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm"
              data-testid="platform-audit-detail"
            >
              {(
                [
                  ["Operator", open.adminEmail],
                  ["Role", open.actorRole],
                  ["Outcome", open.outcome],
                  ["Workspace", open.targetTenantId],
                  [
                    "Target",
                    open.targetType
                      ? `${open.targetType} ${open.targetId ?? ""}`
                      : null,
                  ],
                  ["Address", open.clientIp],
                  ["Browser", open.userAgent],
                  ["Request id", open.requestId],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="break-all">{value ?? "—"}</dd>
                </div>
              ))}
              <dt className="text-muted-foreground">Detail</dt>
              <dd>
                <pre className="whitespace-pre-wrap break-all rounded bg-muted p-2 text-xs">
                  {JSON.stringify(open.detail, null, 2)}
                </pre>
              </dd>
            </dl>
          )}
        </SheetContent>
      </Sheet>
    </Card>
  );
}
