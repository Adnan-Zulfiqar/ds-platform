"use client";

import {
  Bar,
  BarChart,
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
  formatNumber,
} from "@/components/dashboard/charts/chart-theme";
import type { OrdersPoint } from "@/lib/mock/dashboard-data";

interface OrdersChartProps {
  data: readonly OrdersPoint[];
}

/**
 * Order outcomes per week.
 *
 * Stacked bars, because the three states are parts of one whole — the total
 * height is the week's order count, which is itself a number worth reading.
 * Grouped bars would show each state's trend more precisely but lose the total,
 * and the total is the more common question.
 *
 * Colour maps to meaning rather than to series order: green for fulfilled,
 * amber for pending, red for cancelled. The legend text carries the same
 * information, so the chart is still readable without colour.
 */
export function OrdersChart({ data }: OrdersChartProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={[...data]} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="label" {...AXIS_PROPS} />
        <YAxis {...AXIS_PROPS} tickFormatter={formatNumber} width={48} />

        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value: number, name: string) => [formatNumber(value), name]}
          // A translucent block rather than the default: the line cursor is
          // hard to associate with a bar.
          cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
        />

        {/* `stackId` groups the three into one column. Corner rounding is applied
            only to the topmost segment, so the stack reads as a single bar. */}
        <Bar dataKey="fulfilled" name="Fulfilled" stackId="orders" fill={CHART_COLOURS.success} />
        <Bar dataKey="pending" name="Pending" stackId="orders" fill={CHART_COLOURS.warning} />
        <Bar
          dataKey="cancelled"
          name="Cancelled"
          stackId="orders"
          fill={CHART_COLOURS.destructive}
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
