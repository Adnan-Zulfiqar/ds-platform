import type { OrdersPoint, ProductPerformance, TimeSeriesPoint } from "@/lib/mock/dashboard-data";

/**
 * Dashboard data access — **structure only**.
 *
 * No endpoint exists yet. `GET /api/v1/analytics/...` is a registered router
 * with no routes, so implementing fetchers here would produce code that
 * compiles, looks finished, and 404s.
 *
 * What is defined now is the part worth agreeing early: the **query keys** and
 * the **response types**. Both are decisions, not implementation, and settling
 * them means the page can be written against a stable shape.
 *
 * **Migration path when the endpoint lands:**
 * 1. Add the fetchers and `useQuery` hooks below.
 * 2. Swap the `MOCK_*` imports in `app/(protected)/dashboard/page.tsx` for those
 *    hooks.
 * 3. Delete `lib/mock/dashboard-data.ts`. Any surviving `MOCK_` reference means
 *    the migration is incomplete.
 */

/** Period a dashboard query covers. */
export type DashboardPeriod = "7d" | "30d" | "90d" | "12m";

/**
 * Hierarchical query keys.
 *
 * The nesting is what makes partial invalidation work: invalidating
 * `dashboardKeys.all` clears every dashboard query, while a period-specific key
 * leaves the others cached.
 */
export const dashboardKeys = {
  all: ["dashboard"] as const,
  summary: (period: DashboardPeriod) => [...dashboardKeys.all, "summary", period] as const,
  sales: (period: DashboardPeriod) => [...dashboardKeys.all, "sales", period] as const,
  orders: (period: DashboardPeriod) => [...dashboardKeys.all, "orders", period] as const,
  topProducts: (period: DashboardPeriod) =>
    [...dashboardKeys.all, "top-products", period] as const,
};

/** Headline metrics for the stat card row. */
export interface DashboardSummary {
  revenue: number;
  profit: number;
  orderCount: number;
  activeProductCount: number;
  connectedStoreCount: number;
  automationTaskCount: number;
  /** Percentage change against the preceding period of equal length. */
  revenueChangePercent: number;
  profitChangePercent: number;
  orderCountChangePercent: number;
}

export interface DashboardCharts {
  sales: TimeSeriesPoint[];
  orders: OrdersPoint[];
  topProducts: ProductPerformance[];
}
