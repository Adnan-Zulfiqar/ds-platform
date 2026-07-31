"use client";

import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ChartContainerProps {
  title: string;
  description?: string;
  /** Right-aligned controls — range pickers, export buttons. */
  actions?: ReactNode;
  /** Fixed height in pixels. Charts need a bounded parent to render into. */
  height?: number;
  loading?: boolean;
  error?: boolean;
  isEmpty?: boolean;
  emptyMessage?: string;
  onRetry?: () => void;
  className?: string;
  children: ReactNode;
}

/**
 * Frame around a chart, owning its four possible states.
 *
 * Every chart needs the same handling — loading, error, empty, and populated —
 * and putting it here means no individual chart reimplements it, or worse,
 * implements only the happy path and renders a blank rectangle when a request
 * fails.
 *
 * **The height is fixed and applied to the wrapper, not the chart.** Recharts'
 * `ResponsiveContainer` measures its parent; given a parent with no height it
 * collapses to zero and the chart silently disappears. A fixed frame also keeps
 * the skeleton, the error state, and the chart the same size, so the page does
 * not jump between them.
 */
export function ChartContainer({
  title,
  description,
  actions,
  height = 300,
  loading = false,
  error = false,
  isEmpty = false,
  emptyMessage = "No data for this period yet.",
  onRetry,
  className,
  children,
}: ChartContainerProps) {
  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="flex-row items-start justify-between space-y-0 gap-3">
        <div className="min-w-0 space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </CardHeader>

      <CardContent>
        <div style={{ height }} className="w-full">
          {loading ? (
            <div className="h-full w-full" aria-busy="true" aria-label={`Loading ${title}`}>
              <Skeleton className="h-full w-full" />
            </div>
          ) : error ? (
            <ErrorState
              className="h-full border-0 bg-transparent"
              title="Chart unavailable"
              description="This chart could not be loaded."
              onRetry={onRetry}
            />
          ) : isEmpty ? (
            <EmptyState
              className="h-full border-0"
              title="Nothing to show"
              description={emptyMessage}
            />
          ) : (
            children
          )}
        </div>
      </CardContent>
    </Card>
  );
}
