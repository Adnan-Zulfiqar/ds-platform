"use client";

import {
  Bot,
  DollarSign,
  FlaskConical,
  Package,
  ShoppingCart,
  Store,
  TrendingUp,
} from "lucide-react";
import type { ComponentType } from "react";

import { OrderStatisticsCards } from "@/components/orders/order-statistics-cards";
import { OrdersChart } from "@/components/dashboard/charts/orders-chart";
import { ProductPerformanceChart } from "@/components/dashboard/charts/product-performance-chart";
import { SalesChart } from "@/components/dashboard/charts/sales-chart";
import { ChartContainer } from "@/components/dashboard/chart-container";
import { StatCard } from "@/components/dashboard/stat-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import {
  MOCK_ORDERS_SERIES,
  MOCK_PRODUCT_PERFORMANCE,
  MOCK_SALES_SERIES,
  MOCK_STATS,
} from "@/lib/mock/dashboard-data";
import { useAuth } from "@/providers/auth-provider";

/**
 * Dashboard.
 *
 * **The charts and stat row are mock data; the order synchronisation row is
 * live.** The banner says which is which rather than letting an operator
 * mistake invented numbers for their own — a dashboard that looks
 * authoritative and is not is worse than an empty one.
 *
 * The layout, the components, and the states are all real. Replacing the mock
 * import with a React Query hook from `services/dashboard.ts` is the entire
 * migration.
 */

const STAT_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  revenue: DollarSign,
  orders: ShoppingCart,
  profit: TrendingUp,
  products: Package,
  stores: Store,
  automation: Bot,
};

export default function DashboardPage() {
  const { identity } = useAuth();
  const firstName = identity?.user.firstName;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Dashboard"}
        description={
          identity
            ? `Here is what is happening at ${identity.tenant.name}.`
            : "Overview of your store performance."
        }
      />

      {/* Live figures from the order statistics endpoint — real data, unlike
          the sample charts below. Kept above the banner so the "sample data"
          caveat applies only to what actually is sample data. */}
      <section aria-label="Live order status" className="space-y-2">
        <h2 className="text-lg font-semibold">Order synchronisation</h2>
        <OrderStatisticsCards />
      </section>

      <Alert>
        <FlaskConical className="h-4 w-4" />
        <AlertTitle>Sample data</AlertTitle>
        <AlertDescription>
          Every figure below is placeholder data for layout purposes. Real
          metrics appear once products and stores are connected. The order
          synchronisation row above is live.
        </AlertDescription>
      </Alert>

      {/* One column on mobile, two on tablet, three from large up. Six cards
          divide evenly into all three, so no row is ever left ragged. */}
      <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {MOCK_STATS.map((stat) => (
          <StatCard
            key={stat.key}
            label={stat.label}
            value={stat.value}
            icon={STAT_ICONS[stat.key]}
            change={stat.change}
            trend={stat.trend}
            higherIsBetter={stat.higherIsBetter}
            comparisonLabel="vs last month"
          />
        ))}
      </section>

      <section aria-label="Sales overview">
        <ChartContainer
          title="Sales overview"
          description="Revenue and profit over the last twelve months."
          height={320}
        >
          <SalesChart data={MOCK_SALES_SERIES} />
        </ChartContainer>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartContainer
          title="Orders"
          description="Fulfilment outcomes over the last eight weeks."
          height={300}
        >
          <OrdersChart data={MOCK_ORDERS_SERIES} />
        </ChartContainer>

        <ChartContainer
          title="Top products"
          description="Best sellers by units shipped."
          height={300}
        >
          <ProductPerformanceChart data={MOCK_PRODUCT_PERFORMANCE} />
        </ChartContainer>
      </div>
    </div>
  );
}
