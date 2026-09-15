"use client";

import { AlertTriangle, Link2, Package, Store } from "lucide-react";

import { StatCard } from "@/components/dashboard/stat-card";
import { ErrorState } from "@/components/ui/error-state";
import { useStoreStatistics } from "@/services/stores";

export function StoreStatisticsCards() {
  const { data, isLoading, isError, refetch } = useStoreStatistics();

  if (isError) {
    return (
      <ErrorState
        title="Could not load store statistics"
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <section
      aria-label="Store statistics"
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
    >
      <StatCard
        label="Total stores"
        value={String(data?.totalStores ?? 0)}
        icon={Store}
        loading={isLoading}
      />
      <StatCard
        label="Connected"
        value={String(data?.connected ?? 0)}
        icon={Link2}
        loading={isLoading}
      />
      <StatCard
        label="Needs attention"
        value={String(data?.withErrors ?? 0)}
        icon={AlertTriangle}
        loading={isLoading}
        higherIsBetter={false}
      />
      <StatCard
        label="Products linked"
        value={String(data?.productCount ?? 0)}
        icon={Package}
        loading={isLoading}
      />
    </section>
  );
}
