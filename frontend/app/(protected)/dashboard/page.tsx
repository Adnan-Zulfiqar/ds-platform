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
 * **Every figure on this page is mock data.** There is nothing to report yet:
 * no products, orders, or connected stores exist. The banner below says so
 * plainly rather than letting an operator mistake invented numbers for their
 * own — a dashboard that looks authoritative and is not is worse than an empty
 * one.
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

      <Alert>
        <FlaskConical className="h-4 w-4" />
        <AlertTitle>Sample data</AlertTitle>
        <AlertDescription>
          Every figure below is placeholder data for layout purposes. Real
          metrics appear once products, stores, and orders are connected.
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
