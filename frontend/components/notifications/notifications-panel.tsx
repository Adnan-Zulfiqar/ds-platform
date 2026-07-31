"use client";

import { Bell } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/utils";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  useUnreadNotificationCount,
} from "@/services/notifications";

export function NotificationsPanel() {
  const { data: unread } = useUnreadNotificationCount();
  const { data, isLoading, isError, refetch } = useNotifications({
    page: 1,
    size: 50,
    sortBy: "created_at",
    sortDir: "desc",
  });
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();

  if (isError) {
    return (
      <ErrorState
        title="Could not load notifications"
        onRetry={() => void refetch()}
      />
    );
  }

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {unread?.unread ?? 0} unread
        </p>
        <Button
          variant="outline"
          size="sm"
          disabled={!unread?.unread || markAll.isPending}
          onClick={() => markAll.mutate()}
        >
          Mark all read
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Bell}
          title="No notifications yet"
          description="Sync failures, price changes, and automation results appear here."
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {items.map((notification) => (
            <li
              key={notification.id}
              className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between"
            >
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{notification.title}</p>
                  <Badge variant={notification.isRead ? "secondary" : "default"}>
                    {notification.isRead ? "read" : "unread"}
                  </Badge>
                  <Badge variant="outline">{notification.kind}</Badge>
                </div>
                <p className="text-sm text-muted-foreground">{notification.body}</p>
                <p className="text-xs text-muted-foreground">
                  {formatDateTime(notification.createdAt)}
                </p>
                {notification.href && (
                  <Link
                    href={notification.href}
                    className="text-sm text-primary hover:underline"
                  >
                    Open related item
                  </Link>
                )}
              </div>
              {!notification.isRead && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={markRead.isPending}
                  onClick={() => markRead.mutate(notification.id)}
                >
                  Mark read
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
