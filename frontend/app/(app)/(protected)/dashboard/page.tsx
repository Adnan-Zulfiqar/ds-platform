"use client";

import {
  Bot,
  DollarSign,
  Package,
  ShoppingCart,
  Store,
  Warehouse,
} from "lucide-react";
import Link from "next/link";
import type { ComponentType } from "react";

import { OrderStatisticsCards } from "@/components/orders/order-statistics-cards";
import { OrdersChart } from "@/components/dashboard/charts/orders-chart";
import { ProductPerformanceChart } from "@/components/dashboard/charts/product-performance-chart";
import { SalesChart } from "@/components/dashboard/charts/sales-chart";
import { ChartContainer } from "@/components/dashboard/chart-container";
import { StatCard } from "@/components/dashboard/stat-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { formatMoney } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import {
  toProductPerformance,
  toSalesSeries,
  useDashboard,
} from "@/services/dashboard";

/**
 * Dashboard — live analytics from GET /analytics/dashboard.
 *
 * Order synchronisation cards remain a dedicated live row; charts and headline
 * metrics now share the same backend analytics payload. Empty series render as
 * empty states rather than invented sample data.
 */

export default function DashboardPage() {
  const { identity } = useAuth();
  const firstName = identity?.user.firstName;
  const { data, isLoading, isError, refetch } = useDashboard("30d");

  const stats: Array<{
    key: string;
    label: string;
    value: string;
    icon: ComponentType<{ className?: string }>;
  }> = data
    ? [
        {
          key: "revenue",
          label: "Revenue",
          value: formatMoney(data.revenue, "USD"),
          icon: DollarSign,
        },
        {
          key: "orders",
          label: "Orders",
          value: String(data.orderCount),
          icon: ShoppingCart,
        },
        {
          key: "products",
          label: "Products",
          value: String(data.productCount),
          icon: Package,
        },
        {
          key: "stores",
          label: "Stores",
          value: `${data.connectedStoreCount}/${data.storeCount}`,
          icon: Store,
        },
        {
          key: "inventory",
          label: "Inventory units",
          value: String(data.inventoryUnits),
          icon: Warehouse,
        },
        {
          key: "automation",
          label: "Automation runs (7d)",
          value: `${data.automationRuns7d} (${data.automationFailures7d} failed)`,
          icon: Bot,
        },
      ]
    : [];

  return (
    <div className="space-y-6">
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Dashboard"}
        description={
          identity
            ? `Here is what is happening at ${identity.tenant.name}.`
            : "Overview of your store performance."
        }
      />

      <section aria-label="Live order status" className="space-y-2">
        <h2 className="text-lg font-semibold">Order synchronisation</h2>
        <OrderStatisticsCards />
      </section>

      {isError ? (
        <ErrorState
          title="Could not load analytics"
          description="The dashboard could not reach the analytics API."
          onRetry={() => void refetch()}
        />
      ) : (
        <>
          <section
            aria-label="Key metrics"
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            {(isLoading ? Array.from({ length: 6 }, (_, i) => i) : stats).map(
              (stat) =>
                typeof stat === "number" ? (
                  <StatCard key={stat} label="Loading" value="—" loading />
                ) : (
                  <StatCard
                    key={stat.key}
                    label={stat.label}
                    value={stat.value}
                    icon={stat.icon}
                    loading={isLoading}
                  />
                ),
            )}
          </section>

          <section aria-label="Sales overview">
            <ChartContainer
              title="Sales overview"
              description="Revenue and profit for the selected period."
              height={320}
            >
              {data && data.salesSeries.length > 0 ? (
                <SalesChart data={toSalesSeries(data.salesSeries)} />
              ) : isLoading ? null : (
                <EmptyState
                  className="border-0"
                  title="No sales data yet"
                  description="Import orders to see revenue over time."
                />
              )}
            </ChartContainer>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartContainer
              title="Orders"
              description="Fulfilment outcomes for the selected period."
              height={300}
            >
              {data && data.ordersSeries.length > 0 ? (
                <OrdersChart data={data.ordersSeries} />
              ) : isLoading ? null : (
                <EmptyState
                  className="border-0"
                  title="No order series yet"
                  description="Order outcomes appear after synchronisation."
                />
              )}
            </ChartContainer>

            <ChartContainer
              title="Top products"
              description="Best sellers by units sold."
              height={300}
            >
              {data && data.topProducts.length > 0 ? (
                <ProductPerformanceChart
                  data={toProductPerformance(data.topProducts)}
                />
              ) : isLoading ? null : (
                <EmptyState
                  className="border-0"
                  title="No product performance yet"
                  description="Top products appear once orders include line items."
                />
              )}
            </ChartContainer>
          </div>

          {data && data.recentActivity.length > 0 && (
            <section aria-label="Recent activity" className="space-y-3">
              <h2 className="text-lg font-semibold">Recent activity</h2>
              <ul className="divide-y rounded-md border">
                {data.recentActivity.map((item, index) => (
                  <li key={`${item.kind}-${item.occurredAt}-${index}`} className="px-4 py-3">
                    {item.href ? (
                      <Link
                        href={item.href}
                        className="text-sm font-medium hover:underline"
                      >
                        {item.title}
                      </Link>
                    ) : (
                      <p className="text-sm font-medium">{item.title}</p>
                    )}
                    <p className="text-xs text-muted-foreground">{item.kind}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
