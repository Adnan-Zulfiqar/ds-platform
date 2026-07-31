"use client";

import { Bell } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { cn, formatDateTime } from "@/lib/utils";
import {
  useMarkAllNotificationsRead,
  useNotifications,
  useUnreadNotificationCount,
} from "@/services/notifications";

/**
 * Notification centre — backed by GET /notifications.
 *
 * Unread count and the recent list come from React Query (server state). The
 * old Zustand store remains on disk unused so a follow-up can delete it without
 * mixing UI and server state again.
 */
export function NotificationMenu() {
  const { data: unread } = useUnreadNotificationCount();
  const { data: page } = useNotifications({ page: 1, size: 8 });
  const markAll = useMarkAllNotificationsRead();

  const unreadCount = unread?.unread ?? 0;
  const notifications = page?.items ?? [];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={
            unreadCount > 0
              ? `Notifications — ${unreadCount} unread`
              : "Notifications"
          }
        >
          <Bell className="h-4 w-4" />
          {unreadCount > 0 && (
            <span
              aria-hidden="true"
              className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>Notifications</span>
          {unreadCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-auto p-0 text-xs font-normal"
              onClick={() => markAll.mutate()}
              disabled={markAll.isPending}
            >
              Mark all read
            </Button>
          )}
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        {notifications.length === 0 ? (
          <EmptyState
            className="border-0 p-6"
            title="You are all caught up"
            description="Alerts about orders, syncs, and automation will appear here."
          />
        ) : (
          <ul className="max-h-80 overflow-y-auto">
            {notifications.map((notification) => (
              <li
                key={notification.id}
                className={cn(
                  "border-b px-3 py-2.5 last:border-0",
                  !notification.isRead && "bg-accent/40",
                )}
              >
                {notification.href ? (
                  <Link
                    href={notification.href}
                    className="text-sm font-medium hover:underline"
                  >
                    {notification.title}
                  </Link>
                ) : (
                  <p className="text-sm font-medium">{notification.title}</p>
                )}
                {notification.body && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {notification.body}
                  </p>
                )}
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {formatDateTime(notification.createdAt)}
                </p>
              </li>
            ))}
          </ul>
        )}

        <DropdownMenuSeparator />
        <div className="px-2 py-1.5">
          <Button variant="ghost" size="sm" className="w-full" asChild>
            <Link href="/notifications">View all notifications</Link>
          </Button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
