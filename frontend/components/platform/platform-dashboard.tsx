"use client";

import {
  AlertTriangle,
  Building2,
  Package,
  ShoppingCart,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  AXIS_PROPS,
  CHART_COLOURS,
  GRID_PROPS,
  TOOLTIP_STYLE,
  formatNumber,
} from "@/components/dashboard/charts/chart-theme";
import { StatCard } from "@/components/dashboard/stat-card";
import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/utils";
import { type DailyCount, usePlatformDashboard } from "@/services/platform";

/** Admin Control Center phase 2 (D-019): live, platform-wide counts. Every
 * number is a count the API computed from the database a moment ago. */
export function PlatformDashboard() {
  return (
    <RequirePermission permission="dashboard.read">
      <DashboardBody />
    </RequirePermission>
  );
}

const sum = (values: Record<string, number>) =>
  Object.values(values).reduce((a, b) => a + b, 0);

const LABELS: Record<string, string> = {
  order_syncs: "Order syncs",
  inventory_syncs: "Inventory syncs",
  product_imports: "Product imports",
  pipeline_runs: "AI pipeline runs",
  supplier_orders: "Supplier orders",
  notification_emails: "Notification emails",
};

function DashboardBody() {
  const { can } = usePlatformAccess();
  const dashboard = usePlatformDashboard(can("dashboard.read"));

  if (dashboard.isError) {
    return (
      <ErrorState
        title="Could not load the dashboard"
        onRetry={() => void dashboard.refetch()}
      />
    );
  }
  const d = dashboard.data;
  const failed = d ? sum(d.failedLastDay) : 0;
  const stuck = d ? sum(d.stuck) : 0;

  return (
    <>
      <PlatformPageHeader
        title="Dashboard"
        description={
          d
            ? `Live counts, refreshed every minute. As of ${formatDateTime(d.generatedAt)}.`
            : undefined
        }
      />
      <div
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        data-testid="platform-dashboard-stats"
      >
        <StatCard
          label="Workspaces"
          value={d ? formatNumber(sum(d.tenantsByStatus)) : "—"}
          icon={Building2}
          change={d ? `+${d.tenantsNewWeek}` : undefined}
          comparisonLabel="new this week"
          trend={d && d.tenantsNewWeek > 0 ? "up" : "flat"}
          loading={!d}
        />
        <StatCard
          label="Active users"
          value={d ? formatNumber(d.usersActive) : "—"}
          icon={Users}
          change={d ? `+${d.usersNewWeek}` : undefined}
          comparisonLabel="new this week"
          trend={d && d.usersNewWeek > 0 ? "up" : "flat"}
          loading={!d}
        />
        <StatCard
          label="Orders imported, last 24 hours"
          value={d ? formatNumber(d.ordersLastDay) : "—"}
          icon={ShoppingCart}
          change={d ? formatNumber(d.ordersLastWeek) : undefined}
          comparisonLabel="in the last 7 days"
          trend="flat"
          loading={!d}
        />
        <StatCard
          label="Products"
          value={d ? formatNumber(d.productsTotal) : "—"}
          icon={Package}
          change={d ? formatNumber(d.listingsByStatus.error ?? 0) : undefined}
          comparisonLabel="listings in error"
          trend="flat"
          loading={!d}
        />
      </div>

      {!d ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <DailyChart
              title="New workspaces"
              description="Per day, last 30 days"
              data={d.signupsByDay}
            />
            <DailyChart
              title="Orders imported"
              description="Per day, last 14 days"
              data={d.ordersByDay}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card data-testid="platform-dashboard-failures">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <AlertTriangle className="h-4 w-4" aria-hidden />
                  Failures, last 24 hours
                </CardTitle>
                <CardDescription>
                  {failed === 0 ? "Nothing failed." : `${failed} in total.`}
                  {stuck > 0 && ` ${stuck} job(s) look stuck.`}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Counts values={d.failedLastDay} labels={LABELS} />
                {stuck > 0 && (
                  <>
                    <p className="mt-3 text-sm font-medium">Stuck</p>
                    <Counts values={d.stuck} labels={LABELS} />
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Workspaces and stores
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <Counts values={d.tenantsByStatus} />
                <p className="text-sm font-medium">Stores by status</p>
                <Counts values={d.storesByStatus} />
                <p className="text-sm font-medium">Stores by platform</p>
                <Counts values={d.storesByPlatform} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Subscriptions</CardTitle>
                <CardDescription>
                  {d.trialsEndingWeek} trial(s) end in the next 7 days.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Counts values={d.subscriptionsByPlan} />
                <p className="text-sm font-medium">By status</p>
                <Counts values={d.subscriptionsByStatus} />
              </CardContent>
            </Card>
          </div>
          <Card data-testid="platform-dashboard-system">
            <CardHeader>
              <CardTitle className="text-base">System</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-3 text-sm">
              <Badge variant={d.system.database ? "outline" : "destructive"}>
                Database {d.system.database ? "up" : "down"}
              </Badge>
              <Badge variant={d.system.redis ? "outline" : "destructive"}>
                Redis {d.system.redis ? "up" : "down"}
              </Badge>
              <Badge variant="outline">
                Schema {d.system.migrationRevision ?? "unknown"}
              </Badge>
              <Badge variant="outline">
                {d.operatorSessionsOpen} operator session(s) open
              </Badge>
              <Badge
                variant={
                  d.securityFailuresLastDay > 0 ? "destructive" : "outline"
                }
              >
                {d.securityFailuresLastDay} refused operator action(s), 24 h
              </Badge>
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}

function Counts({
  values,
  labels,
}: {
  values: Record<string, number>;
  labels?: Record<string, string>;
}) {
  const entries = Object.entries(values);
  if (entries.length === 0)
    return <p className="text-sm text-muted-foreground">None.</p>;
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted-foreground">
            {labels?.[key] ?? key.replaceAll("_", " ")}
          </dt>
          <dd className="text-right tabular-nums">{formatNumber(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DailyChart({
  title,
  description,
  data,
}: {
  title: string;
  description: string;
  data: DailyCount[];
}) {
  const points = data.map((p) => ({ label: p.day.slice(5), count: p.count }));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={points}
            margin={{ top: 8, right: 8, bottom: 0, left: -20 }}
          >
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="label" {...AXIS_PROPS} />
            <YAxis {...AXIS_PROPS} allowDecimals={false} width={40} />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(v: number) => [formatNumber(v), title]}
            />
            <Bar
              dataKey="count"
              fill={CHART_COLOURS.primary}
              radius={[3, 3, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
