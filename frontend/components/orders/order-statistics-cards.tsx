"use client";

import {
  CheckCircle2,
  Clock,
  Loader2,
  PackageCheck,
  RefreshCw,
  ShoppingCart,
  Webhook,
  XCircle,
} from "lucide-react";

import { StatCard } from "@/components/dashboard/stat-card";
import { formatDateTime } from "@/lib/utils";
import { useOrderStatistics } from "@/services/orders";

/**
 * The live order status row.
 *
 * Shared between the Orders page and the dashboard so the two can never show
 * different numbers for the same tenant. Every figure comes from the
 * statistics endpoint, which computes from the platform's own tables — when
 * something cannot be honestly reported (webhook count without Redis), the
 * card says so instead of showing a fabricated zero.
 */
export function OrderStatisticsCards() {
  const { data, isPending, isError } = useOrderStatistics();

  // A failed statistics call must not take the page down with it: the table
  // below has its own error handling, and stale-looking skeletons are worse
  // than no row at all.
  if (isError) return null;

  const lastSync = data?.lastSync ?? null;
  const lastSyncValue = lastSync
    ? formatDateTime(lastSync.finishedAt ?? lastSync.startedAt ?? lastSync.createdAt)
    : "Never";
  const lastSyncDetail = lastSync
    ? `${lastSync.trigger} · ${lastSync.status}`
    : undefined;

  const webhookValue =
    data?.webhookEventsReceived === null || data?.webhookEventsReceived === undefined
      ? "Unavailable"
      : data.webhookEventsReceived.toLocaleString();

  return (
    <div
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
      data-testid="order-statistics"
    >
      <StatCard
        label="Orders synced"
        value={data?.totalOrders.toLocaleString() ?? ""}
        icon={ShoppingCart}
        loading={isPending}
      />
      <StatCard
        label="Pending fulfilment"
        value={data?.pendingFulfillment.toLocaleString() ?? ""}
        icon={Clock}
        loading={isPending}
      />
      <StatCard
        label="Processing"
        value={data?.processing.toLocaleString() ?? ""}
        icon={Loader2}
        loading={isPending}
      />
      <StatCard
        label="Delivered"
        value={data?.delivered.toLocaleString() ?? ""}
        icon={PackageCheck}
        loading={isPending}
      />
      <StatCard
        label="Failed syncs (7 days)"
        value={data?.failedSyncsLast7Days.toLocaleString() ?? ""}
        icon={XCircle}
        loading={isPending}
      />
      <StatCard
        label="Last synchronisation"
        value={lastSyncValue}
        change={lastSyncDetail}
        trend={lastSync?.status === "failed" ? "down" : "flat"}
        higherIsBetter={false}
        icon={RefreshCw}
        loading={isPending}
      />
      <StatCard
        label="Webhook activity"
        value={webhookValue}
        icon={Webhook}
        loading={isPending}
      />
      <StatCard
        label="Background jobs"
        value={
          lastSync?.trigger === "scheduled"
            ? "Active"
            : lastSync
              ? "Manual only"
              : "Idle"
        }
        icon={CheckCircle2}
        loading={isPending}
      />
    </div>
  );
}
