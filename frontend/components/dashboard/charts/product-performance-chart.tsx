"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  AXIS_PROPS,
  CHART_COLOURS,
  TOOLTIP_STYLE,
  formatNumber,
} from "@/components/dashboard/charts/chart-theme";
import type { ProductPerformance } from "@/lib/mock/dashboard-data";

interface ProductPerformanceChartProps {
  data: readonly ProductPerformance[];
}

/**
 * Best-selling products by units shipped.
 *
 * **Horizontal bars, deliberately.** Product names are long; on a vertical
 * chart they would be rotated, truncated, or both. Laid horizontally the label
 * runs along the axis and stays readable, which matters more here than the
 * slightly easier value comparison a vertical chart gives.
 *
 * Data arrives pre-sorted from the caller. Sorting inside the component would
 * hide an ordering decision that belongs to whoever chose the data.
 */
export function ProductPerformanceChart({ data }: ProductPerformanceChartProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={[...data]}
        layout="vertical"
        margin={{ top: 4, right: 16, bottom: 0, left: 8 }}
      >
        {/* Vertical grid lines only: on a horizontal chart those are the ones
            that run along the value axis and actually aid comparison. */}
        <CartesianGrid stroke={CHART_COLOURS.grid} strokeDasharray="3 3" horizontal={false} />

        <XAxis type="number" {...AXIS_PROPS} tickFormatter={formatNumber} />
        <YAxis
          type="category"
          dataKey="name"
          {...AXIS_PROPS}
          width={140}
          // Truncate rather than let a long name push the plot area to nothing.
          // The tooltip shows the full name.
          tickFormatter={(name: string) =>
            name.length > 18 ? `${name.slice(0, 17)}…` : name
          }
        />

        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value: number) => [formatNumber(value), "Units"]}
          cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }}
        />

        <Bar
          dataKey="units"
          name="Units"
          fill={CHART_COLOURS.primary}
          radius={[0, 4, 4, 0]}
          // Capped so five rows do not render as five slabs filling the frame.
          maxBarSize={28}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
