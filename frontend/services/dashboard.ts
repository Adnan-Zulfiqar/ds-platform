import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";

/**
 * Dashboard data access — real analytics from GET /analytics/dashboard.
 *
 * Chart component types live here (formerly under `lib/mock/dashboard-data`,
 * which was deleted once the dashboard consumed this API).
 */

export type DashboardPeriod = "7d" | "30d" | "90d" | "12m";

export interface TimeSeriesPoint {
  label: string;
  revenue: number;
  profit: number;
}

export interface OrdersPoint {
  label: string;
  fulfilled: number;
  pending: number;
  cancelled: number;
}

export interface ProductPerformance {
  name: string;
  units: number;
}

export const dashboardKeys = {
  all: ["dashboard"] as const,
  summary: (period: DashboardPeriod) =>
    [...dashboardKeys.all, "summary", period] as const,
};

export interface AnalyticsSeriesPoint {
  label: string;
  revenue: string;
  orders: number;
  profit: string;
}

export interface AnalyticsOrdersPoint {
  label: string;
  fulfilled: number;
  pending: number;
  cancelled: number;
}

export interface AnalyticsTopProduct {
  productId: string;
  title: string;
  units: number;
  revenue: string | null;
}

export interface AnalyticsRecentActivity {
  kind: string;
  title: string;
  occurredAt: string;
  href: string | null;
}

export interface AnalyticsDashboard {
  revenue: string;
  orderCount: number;
  productCount: number;
  storeCount: number;
  connectedStoreCount: number;
  inventoryUnits: number;
  syncRuns7d: number;
  syncFailures7d: number;
  automationRuns7d: number;
  automationFailures7d: number;
  unreadNotifications: number;
  salesSeries: AnalyticsSeriesPoint[];
  ordersSeries: AnalyticsOrdersPoint[];
  topProducts: AnalyticsTopProduct[];
  recentActivity: AnalyticsRecentActivity[];
  periodStart: string;
  periodEnd: string;
}

const PERIOD_DAYS: Record<DashboardPeriod, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  "12m": 365,
};

async function fetchDashboard(period: DashboardPeriod): Promise<AnalyticsDashboard> {
  const { data } = await apiClient.get<AnalyticsDashboard>("/analytics/dashboard", {
    params: { periodDays: PERIOD_DAYS[period] },
  });
  return data;
}

export function useDashboard(
  period: DashboardPeriod = "30d",
): UseQueryResult<AnalyticsDashboard> {
  return useQuery({
    queryKey: dashboardKeys.summary(period),
    queryFn: () => fetchDashboard(period),
  });
}

export function toSalesSeries(
  points: AnalyticsSeriesPoint[],
): TimeSeriesPoint[] {
  return points.map((point) => ({
    label: point.label,
    revenue: Number(point.revenue) || 0,
    profit: Number(point.profit) || 0,
  }));
}

export function toProductPerformance(
  products: AnalyticsTopProduct[],
): ProductPerformance[] {
  return products.map((product) => ({
    name: product.title,
    units: product.units,
  }));
}
