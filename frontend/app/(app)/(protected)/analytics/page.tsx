"use client";

import {
  Bot,
  DollarSign,
  Package,
  ShoppingCart,
  Store,
  Warehouse,
} from "lucide-react";

import { OrdersChart } from "@/components/dashboard/charts/orders-chart";
import { ProductPerformanceChart } from "@/components/dashboard/charts/product-performance-chart";
import { SalesChart } from "@/components/dashboard/charts/sales-chart";
import { ChartContainer } from "@/components/dashboard/chart-container";
import { StatCard } from "@/components/dashboard/stat-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { formatMoney } from "@/lib/utils";
import {
  toProductPerformance,
  toSalesSeries,
  useDashboard,
  type DashboardPeriod,
} from "@/services/dashboard";
import { useState } from "react";

const PERIODS: Array<{ value: DashboardPeriod; label: string }> = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "12m", label: "12 months" },
];

/**
 * Distinct from the dashboard: period selection and deeper operational metrics
 * (sync / automation failures) live here. Both pages share the same live API.
 */
export default function AnalyticsPage() {
  const [period, setPeriod] = useState<DashboardPeriod>("30d");
  const { data, isLoading, isError, refetch } = useDashboard(period);

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Analytics"
        description="Revenue, orders, inventory, sync activity, and automation health."
        actions={
          <select
            aria-label="Period"
            value={period}
            onChange={(event) => setPeriod(event.target.value as DashboardPeriod)}
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          >
            {PERIODS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        }
      />

      {isError ? (
        <ErrorState
          title="Could not load analytics"
          onRetry={() => void refetch()}
        />
      ) : (
        <>
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard
              label="Revenue"
              value={formatMoney(data?.revenue, "USD")}
              icon={DollarSign}
              loading={isLoading}
            />
            <StatCard
              label="Orders"
              value={String(data?.orderCount ?? 0)}
              icon={ShoppingCart}
              loading={isLoading}
            />
            <StatCard
              label="Products"
              value={String(data?.productCount ?? 0)}
              icon={Package}
              loading={isLoading}
            />
            <StatCard
              label="Stores"
              value={`${data?.connectedStoreCount ?? 0}/${data?.storeCount ?? 0}`}
              icon={Store}
              loading={isLoading}
            />
            <StatCard
              label="Inventory units"
              value={String(data?.inventoryUnits ?? 0)}
              icon={Warehouse}
              loading={isLoading}
            />
            <StatCard
              label="Sync failures (7d)"
              value={`${data?.syncFailures7d ?? 0} / ${data?.syncRuns7d ?? 0}`}
              icon={Bot}
              loading={isLoading}
              higherIsBetter={false}
            />
          </section>

          <ChartContainer
            title="Sales"
            description={`Period ${data?.periodStart ?? "—"} to ${data?.periodEnd ?? "—"}.`}
            height={320}
          >
            {data && data.salesSeries.length > 0 ? (
              <SalesChart data={toSalesSeries(data.salesSeries)} />
            ) : isLoading ? null : (
              <EmptyState
                className="border-0"
                title="No sales series"
                description="Figures appear after orders are imported."
              />
            )}
          </ChartContainer>

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartContainer title="Orders" height={300}>
              {data && data.ordersSeries.length > 0 ? (
                <OrdersChart data={data.ordersSeries} />
              ) : isLoading ? null : (
                <EmptyState
                  className="border-0"
                  title="No order series"
                  description="Fulfilment outcomes appear after synchronisation."
                />
              )}
            </ChartContainer>
            <ChartContainer title="Top products" height={300}>
              {data && data.topProducts.length > 0 ? (
                <ProductPerformanceChart
                  data={toProductPerformance(data.topProducts)}
                />
              ) : isLoading ? null : (
                <EmptyState
                  className="border-0"
                  title="No top products"
                  description="Best sellers appear once line items exist."
                />
              )}
            </ChartContainer>
          </div>
        </>
      )}
    </div>
  );
}
