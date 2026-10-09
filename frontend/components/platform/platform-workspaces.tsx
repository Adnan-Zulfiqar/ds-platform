"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { SupportSessionBanner } from "@/components/platform/platform-workspace-actions";
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
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { cn, formatDateTime } from "@/lib/utils";
import {
  type PlatformWorkspaceOverview,
  usePlatformTenants,
  usePlatformWorkspace,
  useSetTenantActive,
} from "@/services/platform";

export function PlatformWorkspaceList() {
  return (
    <RequirePermission permission="tenants.read">
      <WorkspaceList />
    </RequirePermission>
  );
}

function WorkspaceList() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [page, setPage] = useState(1);
  const tenants = usePlatformTenants(submitted, page, true);

  return (
    <>
      <PlatformPageHeader
        title="Workspaces"
        description="Every workspace on the platform."
      />
      <form
        className="flex max-w-xl gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
          setSubmitted(query.trim());
        }}
      >
        <Input
          aria-label="Search workspaces"
          placeholder="Search by name or slug"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" variant="outline">
          Search
        </Button>
      </form>
      {tenants.isError ? (
        <ErrorState
          title="Could not load workspaces"
          onRetry={() => void tenants.refetch()}
        />
      ) : !tenants.data ? (
        <Skeleton className="h-64 w-full" />
      ) : tenants.data.items.length === 0 ? (
        <EmptyState
          title="No workspaces"
          description={
            submitted
              ? "Nothing matches that search."
              : "No workspace exists yet."
          }
        />
      ) : (
        <>
          <ul
            className="divide-y rounded-lg border"
            data-testid="platform-tenants"
          >
            {tenants.data.items.map((tenant) => (
              <li key={tenant.id}>
                <Link
                  href={`/platform/workspaces/${tenant.id}`}
                  className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-accent/40"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">
                      {tenant.name}
                    </span>
                    <span className="block truncate text-muted-foreground">
                      {tenant.slug} · {tenant.users} users ·{" "}
                      {tenant.connectedStores} connected stores · created{" "}
                      {formatDateTime(tenant.createdAt)}
                    </span>
                  </span>
                  <Badge variant={tenant.isActive ? "outline" : "destructive"}>
                    {tenant.status}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
          <Pager
            page={page}
            totalPages={tenants.data.meta.totalPages}
            hasNext={tenants.data.meta.hasNext}
            onPage={setPage}
          />
        </>
      )}
    </>
  );
}

export function Pager({
  page,
  totalPages,
  hasNext,
  onPage,
}: {
  page: number;
  totalPages: number;
  hasNext: boolean;
  onPage: (page: number) => void;
}) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Button
        size="sm"
        variant="outline"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        Previous
      </Button>
      <span>
        Page {page} of {Math.max(1, totalPages)}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={!hasNext}
        onClick={() => onPage(page + 1)}
      >
        Next
      </Button>
    </div>
  );
}

/** One workspace (D-019). Each tab is a server-checked, audited view. */
export function PlatformWorkspaceDetail({
  tenantId,
  tabs,
}: {
  tenantId: string;
  /** Extra tabs from later phases: label → content. */
  tabs?: { id: string; label: string; content: ReactNode }[];
}) {
  const { can } = usePlatformAccess();
  const allowed = can("workspace.data.read");
  const overview = usePlatformWorkspace(tenantId, allowed);
  const [tab, setTab] = useState("overview");
  const extra = tabs ?? [];

  return (
    <>
      <Link
        href="/platform/workspaces"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden /> All workspaces
      </Link>
      {!allowed ? (
        <Alert>
          <AlertDescription>
            Your role cannot see inside workspaces.
          </AlertDescription>
        </Alert>
      ) : overview.isError ? (
        <ErrorState
          title={
            overview.error instanceof ApiError && overview.error.status === 404
              ? "This workspace does not exist"
              : "Could not load the workspace"
          }
          onRetry={() => void overview.refetch()}
        />
      ) : !overview.data ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <PlatformPageHeader
            title={overview.data.name}
            description={`${overview.data.slug} · created ${formatDateTime(overview.data.createdAt)} · every view is audited`}
            actions={
              <Badge
                variant={overview.data.isActive ? "outline" : "destructive"}
              >
                {overview.data.status}
              </Badge>
            }
          />
          <SupportSessionBanner tenantId={tenantId} />
          <nav
            aria-label="Workspace sections"
            className="flex flex-wrap gap-1 border-b"
          >
            {[{ id: "overview", label: "Overview" }, ...extra].map((t) => (
              <button
                key={t.id}
                type="button"
                aria-current={tab === t.id ? "page" : undefined}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-sm",
                  tab === t.id
                    ? "border-primary font-medium"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {tab === "overview" ? (
            <Overview data={overview.data} />
          ) : (
            extra.find((t) => t.id === tab)?.content
          )}
        </>
      )}
    </>
  );
}

function Overview({ data }: { data: PlatformWorkspaceOverview }) {
  const { can } = usePlatformAccess();
  return (
    <div
      className="grid gap-4 lg:grid-cols-3"
      data-testid="platform-workspace-overview"
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-base">People and catalogue</CardTitle>
        </CardHeader>
        <CardContent>
          <Facts
            rows={[
              ["Users", `${data.users} (${data.activeUsers} active)`],
              ["Draft products", data.products.drafts ?? 0],
              ["Published products", data.products.products ?? 0],
              ["Timezone", data.timezone ?? "—"],
              ["Currency", data.defaultCurrency ?? "—"],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stores, listings, orders</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Facts
            rows={Object.entries(data.storesByStatus).map(([k, v]) => [
              `Stores ${k}`,
              v,
            ])}
          />
          <Facts
            rows={Object.entries(data.listingsByStatus).map(([k, v]) => [
              `Listings ${k}`,
              v,
            ])}
          />
          <Facts
            rows={Object.entries(data.ordersByStatus).map(([k, v]) => [
              `Orders ${k}`,
              v,
            ])}
          />
        </CardContent>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Subscription</CardTitle>
          </CardHeader>
          <CardContent>
            {data.subscription ? (
              <Facts
                rows={[
                  ["Plan", data.subscription.plan ?? "trial"],
                  ["Status", data.subscription.status],
                  ["Trial ends", formatDateTime(data.subscription.trialEndsAt)],
                  [
                    "Period ends",
                    data.subscription.currentPeriodEnd
                      ? formatDateTime(data.subscription.currentPeriodEnd)
                      : "—",
                  ],
                  ["AI add-on", data.subscription.aiAddon ? "yes" : "no"],
                  [
                    "Stripe customer",
                    data.subscription.hasStripeCustomer ? "yes" : "no",
                  ],
                ]}
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                No subscription row yet.
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Last {data.health.windowHours} hours
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Facts
              testId="platform-tenant-health"
              rows={[
                ["Failed order syncs", data.health.failedOrderSyncs],
                ["Failed inventory syncs", data.health.failedInventorySyncs],
                ["Listings in error", data.health.listingsInError],
                [
                  "Failed notification emails",
                  data.health.failedNotificationEmails,
                ],
              ]}
            />
          </CardContent>
        </Card>
        {can("tenants.suspend") && <SuspendControl data={data} />}
      </div>
    </div>
  );
}

export function Facts({
  rows,
  testId,
}: {
  rows: [string, ReactNode][];
  testId?: string;
}) {
  if (rows.length === 0)
    return <p className="text-sm text-muted-foreground">None.</p>;
  return (
    <dl
      className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm"
      data-testid={testId}
    >
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="text-right tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function SuspendControl({ data }: { data: PlatformWorkspaceOverview }) {
  const change = useSetTenantActive();
  const guard = useReauthGuard();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nextActive = !data.isActive;

  function submit() {
    setError(null);
    guard(() =>
      change.mutateAsync({
        tenantId: data.id,
        active: nextActive,
        reason: reason.trim(),
      }),
    ).then(
      () => setReason(""),
      (e: unknown) => {
        if (e instanceof ApiError && e.code === "reauth_cancelled") return;
        setError("The change was not made.");
      },
    );
  }

  return (
    <Card data-testid="platform-tenant-panel">
      <CardHeader>
        <CardTitle className="text-base">
          {nextActive ? "Reactivate" : "Suspend"} workspace
        </CardTitle>
        <CardDescription>
          {nextActive
            ? "Its users can sign in again."
            : "Its users are signed out within 15 minutes and cannot sign in."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Label htmlFor="platform-reason">
          Reason (recorded in the audit log)
        </Label>
        <Input
          id="platform-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Button
          variant={nextActive ? "default" : "destructive"}
          disabled={reason.trim().length < 3 || change.isPending}
          onClick={submit}
        >
          {nextActive ? "Reactivate workspace" : "Suspend workspace"}
        </Button>
      </CardContent>
    </Card>
  );
}
