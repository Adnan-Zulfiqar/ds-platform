"use client";

import { History } from "lucide-react";

import { statusLabel } from "@/components/orders/order-status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, formatDateTime } from "@/lib/utils";
import { useOrderTimeline } from "@/services/orders";
import type { OrderTimelineEntry } from "@/types/api";

/**
 * One chronological story: lifecycle events and carrier scans, merged
 * server-side. The dot colour distinguishes the two sources without splitting
 * the list back into the streams the server just joined.
 */

function entryTitle(entry: OrderTimelineEntry): string {
  if (entry.kind === "tracking") {
    return entry.status ? statusLabel(entry.status) : "Tracking update";
  }
  if (entry.eventType === "status_change" && entry.fromStatus && entry.toStatus) {
    return `${statusLabel(entry.fromStatus)} → ${statusLabel(entry.toStatus)}`;
  }
  return entry.eventType ? statusLabel(entry.eventType) : "Event";
}

export function OrderTimeline({ orderId }: { orderId: string }) {
  const { data, isPending, isError, error, refetch } = useOrderTimeline(orderId);

  if (isPending) {
    return (
      <div className="space-y-2" data-testid="timeline-loading">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load the timeline"
        description={error instanceof Error ? error.message : "Please try again."}
        onRetry={() => void refetch()}
      />
    );
  }

  if (data.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No history yet"
        description="Events appear here as the order moves through fulfilment."
      />
    );
  }

  return (
    <ol className="space-y-0" data-testid="order-timeline">
      {data.map((entry, index) => (
        <li key={`${entry.occurredAt}-${index}`} className="relative flex gap-3 pb-6 last:pb-0">
          {index < data.length - 1 && (
            <span
              className="absolute left-[5px] top-4 h-full w-px bg-border"
              aria-hidden="true"
            />
          )}
          <span
            className={cn(
              "mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full border-2 border-background",
              entry.kind === "tracking" ? "bg-muted-foreground" : "bg-primary",
            )}
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-sm font-medium capitalize">{entryTitle(entry)}</p>
            {entry.description && (
              <p className="text-sm text-muted-foreground">{entry.description}</p>
            )}
            <p className="mt-0.5 text-xs text-muted-foreground">
              {formatDateTime(entry.occurredAt)}
              {entry.location ? ` · ${entry.location}` : ""}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
