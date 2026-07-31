"use client";

import { Bell } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { useNotificationStore } from "@/stores/notification-store";
import { cn } from "@/lib/utils";

/**
 * Notification centre.
 *
 * The panel and its store are real; **there is no notification source yet**, so
 * it is genuinely empty rather than populated with invented entries. A fake
 * unread badge on an empty inbox trains users to ignore the badge, which is
 * precisely the wrong habit to build into a product whose value later depends
 * on alerting people to failed order syncs.
 *
 * The store shape is in place so the phase that produces notifications only
 * has to feed it.
 */
export function NotificationMenu() {
  const notifications = useNotificationStore((state) => state.notifications);
  const unreadCount = useNotificationStore((state) => state.unreadCount());
  const markAllRead = useNotificationStore((state) => state.markAllRead);

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
              // Decorative: the count is already in the button's accessible
              // name above, so announcing it twice would be noise.
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
              onClick={markAllRead}
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
                  !notification.read && "bg-accent/40",
                )}
              >
                <p className="text-sm font-medium">{notification.title}</p>
                {notification.body && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {notification.body}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
