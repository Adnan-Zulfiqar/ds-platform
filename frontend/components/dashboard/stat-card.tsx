import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import type { ComponentType } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export type TrendDirection = "up" | "down" | "flat";

export interface StatCardProps {
  label: string;
  value: string;
  icon?: ComponentType<{ className?: string }>;
  /** Change against the comparison period, e.g. "+12.5%". */
  change?: string;
  trend?: TrendDirection;
  /** What the change is measured against, e.g. "vs last month". */
  comparisonLabel?: string;
  /**
   * Whether a rise is good.
   *
   * Not every metric improves by going up — refund rate and cost per order get
   * worse. Colouring purely by direction would tell the user a rising refund
   * rate is good news.
   */
  higherIsBetter?: boolean;
  loading?: boolean;
  className?: string;
}

const TREND_ICONS: Record<TrendDirection, ComponentType<{ className?: string }>> = {
  up: ArrowUp,
  down: ArrowDown,
  flat: Minus,
};

/**
 * A single headline metric.
 *
 * The building block of the dashboard's top row. Three details that matter more
 * than they look:
 *
 * * **Colour is never the only signal.** The arrow icon and the sign in the
 *   text both convey direction, so the card still reads correctly in monochrome
 *   and for colour-blind users.
 * * **`higherIsBetter` decouples direction from sentiment**, so a metric where
 *   down is good is not painted red.
 * * **The loading state occupies the same box.** Swapping in a smaller spinner
 *   would make the whole row jump when data arrives.
 */
export function StatCard({
  label,
  value,
  icon: Icon,
  change,
  trend = "flat",
  comparisonLabel,
  higherIsBetter = true,
  loading = false,
  className,
}: StatCardProps) {
  const TrendIcon = TREND_ICONS[trend];

  const isPositive =
    trend === "flat" ? null : (trend === "up") === higherIsBetter;

  const trendColour =
    isPositive === null
      ? "text-muted-foreground"
      : isPositive
        ? "text-success"
        : "text-destructive";

  return (
    <Card className={className}>
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          {Icon && (
            <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          )}
        </div>

        {loading ? (
          <div className="mt-3 space-y-2">
            <Skeleton className="h-8 w-28" />
            <Skeleton className="h-4 w-36" />
          </div>
        ) : (
          <>
            <p className="mt-2 text-2xl font-semibold tracking-tight tabular-nums">
              {/* tabular-nums stops the digits shifting width as values change,
                  which otherwise makes a row of cards visibly twitch. */}
              {value}
            </p>

            {change && (
              <p className="mt-1.5 flex items-center gap-1 text-xs">
                <TrendIcon className={cn("h-3.5 w-3.5", trendColour)} aria-hidden="true" />
                <span className={cn("font-medium", trendColour)}>{change}</span>
                {comparisonLabel && (
                  <span className="text-muted-foreground">{comparisonLabel}</span>
                )}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
