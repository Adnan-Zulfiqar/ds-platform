/**
 * ============================================================================
 * MOCK DATA — NOT REAL, NOT FROM AN API
 * ============================================================================
 *
 * Every value in this file is invented. It exists so the dashboard shell can be
 * built, reviewed, and tested before any of the underlying features exist.
 *
 * **This module is quarantined deliberately.** It lives under `lib/mock/`,
 * nothing outside the dashboard imports it, and every export is prefixed
 * `MOCK_`. When the real endpoints arrive, the replacement is mechanical:
 * swap the import in the dashboard page for a React Query hook from
 * `services/dashboard.ts`, then delete this file. If a `MOCK_` symbol still
 * appears anywhere after that, the migration is incomplete — which is the whole
 * point of the naming.
 *
 * The dashboard also renders a visible banner stating the figures are
 * placeholders, so nobody mistakes them for their own data.
 */

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

/** Twelve months of revenue and profit. */
export const MOCK_SALES_SERIES: readonly TimeSeriesPoint[] = [
  { label: "Feb", revenue: 18_400, profit: 5_120 },
  { label: "Mar", revenue: 22_150, profit: 6_480 },
  { label: "Apr", revenue: 19_800, profit: 5_640 },
  { label: "May", revenue: 26_300, profit: 8_010 },
  { label: "Jun", revenue: 31_720, profit: 9_940 },
  { label: "Jul", revenue: 29_450, profit: 8_820 },
  { label: "Aug", revenue: 35_600, profit: 11_380 },
  { label: "Sep", revenue: 38_900, profit: 12_640 },
  { label: "Oct", revenue: 42_100, profit: 13_970 },
  { label: "Nov", revenue: 58_300, profit: 19_820 },
  { label: "Dec", revenue: 64_750, profit: 22_140 },
  { label: "Jan", revenue: 47_200, profit: 15_390 },
];

/** Order outcomes over the last eight weeks. */
export const MOCK_ORDERS_SERIES: readonly OrdersPoint[] = [
  { label: "W1", fulfilled: 142, pending: 18, cancelled: 6 },
  { label: "W2", fulfilled: 168, pending: 22, cancelled: 4 },
  { label: "W3", fulfilled: 155, pending: 15, cancelled: 9 },
  { label: "W4", fulfilled: 191, pending: 27, cancelled: 5 },
  { label: "W5", fulfilled: 204, pending: 19, cancelled: 7 },
  { label: "W6", fulfilled: 187, pending: 24, cancelled: 3 },
  { label: "W7", fulfilled: 221, pending: 31, cancelled: 8 },
  { label: "W8", fulfilled: 246, pending: 26, cancelled: 5 },
];

/** Best-selling products by units shipped. */
export const MOCK_PRODUCT_PERFORMANCE: readonly ProductPerformance[] = [
  { name: "Wireless Earbuds Pro", units: 486 },
  { name: "Adjustable Laptop Stand", units: 412 },
  { name: "Portable Blender", units: 358 },
  { name: "LED Strip Lights 5m", units: 291 },
  { name: "Magnetic Phone Mount", units: 247 },
];

export interface MockStat {
  key: string;
  label: string;
  value: string;
  change: string;
  trend: "up" | "down" | "flat";
  higherIsBetter: boolean;
}

/** The six headline metrics on the dashboard. */
export const MOCK_STATS: readonly MockStat[] = [
  {
    key: "revenue",
    label: "Revenue",
    value: "$47,204",
    change: "+12.4%",
    trend: "up",
    higherIsBetter: true,
  },
  {
    key: "orders",
    label: "Orders",
    value: "1,284",
    change: "+8.1%",
    trend: "up",
    higherIsBetter: true,
  },
  {
    key: "profit",
    label: "Profit",
    value: "$15,390",
    change: "-2.3%",
    trend: "down",
    higherIsBetter: true,
  },
  {
    key: "products",
    label: "Active products",
    value: "342",
    change: "+24",
    trend: "up",
    higherIsBetter: true,
  },
  {
    key: "stores",
    label: "Connected stores",
    value: "3",
    change: "No change",
    trend: "flat",
    higherIsBetter: true,
  },
  {
    key: "automation",
    label: "Automation tasks",
    value: "18",
    change: "+3",
    trend: "up",
    higherIsBetter: true,
  },
];
