"use client";

import Link from "next/link";

import type { AppNotification } from "@/services/notifications";

import { NOTIFICATION_KIND_LABEL, formatRelativeTime } from "./home-rules";

interface RecentActivityProps {
  notifications: AppNotification[];
}

/**
 * "What happened recently?" — the latest notifications, named as such.
 *
 * The backend's `recentActivity` on the analytics payload is these same
 * notifications re-labelled, so Home reads the authoritative source directly
 * and does not stitch unrelated timestamps into a pretend audit trail.
 */
export function RecentActivity({ notifications }: RecentActivityProps) {
  if (notifications.length === 0) {
    return (
      <p className="rounded-md border bg-card px-4 py-3 text-sm text-muted-foreground" data-testid="recent-activity-empty">
        No activity yet. Imports, syncs and automation runs will be listed here.
      </p>
    );
  }

  return (
    <ul className="divide-y rounded-md border bg-card" data-testid="recent-activity">
      {notifications.map((item) => (
        <li key={item.id} className="px-4 py-3">
          <div className="flex items-baseline justify-between gap-3">
            <p className="min-w-0 truncate text-sm font-medium">
              {item.href ? (
                <Link href={item.href} className="underline-offset-4 hover:underline">
                  {item.title}
                </Link>
              ) : (
                item.title
              )}
            </p>
            <time dateTime={item.createdAt} className="shrink-0 text-xs text-muted-foreground">
              {formatRelativeTime(item.createdAt)}
            </time>
          </div>
          <p className="text-xs text-muted-foreground">
            {NOTIFICATION_KIND_LABEL[item.kind] ?? item.kind}
            {item.isRead ? "" : " · unread"}
          </p>
        </li>
      ))}
    </ul>
  );
}
