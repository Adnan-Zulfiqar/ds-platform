/**
 * Shared chart styling.
 *
 * **Charts cannot use Tailwind classes for their marks.** Recharts renders SVG
 * and takes colours as props, so the design tokens have to be read from CSS
 * custom properties at runtime instead.
 *
 * `hsl(var(--primary))` works directly in an SVG `fill` or `stroke`, which is
 * what keeps chart colours in step with the rest of the design system — and
 * means a theme switch recolours the charts with no JavaScript, because the
 * variables themselves change.
 */

export const CHART_COLOURS = {
  primary: "hsl(var(--primary))",
  success: "hsl(var(--success))",
  warning: "hsl(var(--warning))",
  destructive: "hsl(var(--destructive))",
  muted: "hsl(var(--muted-foreground))",
  grid: "hsl(var(--border))",
} as const;

/**
 * A qualitative palette for series without an inherent meaning.
 *
 * Ordered so adjacent entries stay distinguishable for the most common forms of
 * colour blindness — blue and amber separate far better than the red/green pair
 * that dashboards reach for by default.
 */
export const CHART_SERIES_COLOURS = [
  CHART_COLOURS.primary,
  CHART_COLOURS.success,
  CHART_COLOURS.warning,
  CHART_COLOURS.destructive,
] as const;

/** Axis and grid defaults, applied consistently across every chart. */
export const AXIS_PROPS = {
  stroke: CHART_COLOURS.muted,
  fontSize: 12,
  tickLine: false,
  axisLine: false,
} as const;

export const GRID_PROPS = {
  stroke: CHART_COLOURS.grid,
  strokeDasharray: "3 3",
  // Horizontal only. Vertical grid lines add clutter without helping a reader
  // compare values along a categorical axis.
  vertical: false,
} as const;

/**
 * Tooltip styling.
 *
 * Recharts renders its tooltip as an inline-styled div rather than SVG, so it
 * needs the resolved token values here to match the popover surface used
 * everywhere else.
 */
export const TOOLTIP_STYLE = {
  backgroundColor: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "var(--radius)",
  color: "hsl(var(--popover-foreground))",
  fontSize: "12px",
  boxShadow: "0 4px 12px rgb(0 0 0 / 0.08)",
} as const;

/** Compact currency, e.g. `$47.2k`. Full precision belongs in the tooltip. */
export function formatCurrencyCompact(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}
