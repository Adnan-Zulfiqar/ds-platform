"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
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
  formatCurrency,
  formatCurrencyCompact,
} from "@/components/dashboard/charts/chart-theme";
import type { TimeSeriesPoint } from "@/lib/mock/dashboard-data";

interface SalesChartProps {
  data: readonly TimeSeriesPoint[];
}

/**
 * Revenue and profit over time.
 *
 * An area chart rather than lines: the filled region makes the gap between
 * revenue and profit — the margin, which is the number that actually matters to
 * a dropshipping operator — readable at a glance.
 *
 * Axis ticks are abbreviated (`$47.2k`) to keep the axis narrow; the tooltip
 * shows full precision, so nothing is lost.
 */
export function SalesChart({ data }: SalesChartProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={[...data]} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
        <defs>
          {/* Gradient fills keep the overlapping areas readable. A flat fill at
              the same opacity turns the overlap into mud. */}
          <linearGradient id="salesRevenue" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_COLOURS.primary} stopOpacity={0.28} />
            <stop offset="100%" stopColor={CHART_COLOURS.primary} stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="salesProfit" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_COLOURS.success} stopOpacity={0.28} />
            <stop offset="100%" stopColor={CHART_COLOURS.success} stopOpacity={0.02} />
          </linearGradient>
        </defs>

        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="label" {...AXIS_PROPS} />
        <YAxis {...AXIS_PROPS} tickFormatter={formatCurrencyCompact} width={56} />

        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value: number, name: string) => [formatCurrency(value), name]}
          cursor={{ stroke: CHART_COLOURS.grid }}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
        />

        <Area
          type="monotone"
          dataKey="revenue"
          name="Revenue"
          stroke={CHART_COLOURS.primary}
          strokeWidth={2}
          fill="url(#salesRevenue)"
        />
        <Area
          type="monotone"
          dataKey="profit"
          name="Profit"
          stroke={CHART_COLOURS.success}
          strokeWidth={2}
          fill="url(#salesProfit)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
