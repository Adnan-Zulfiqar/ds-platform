import { create } from "zustand";

/**
 * Notification state.
 *
 * **Structure only — nothing produces notifications yet.** The store exists so
 * the phase that adds a source (order sync failures, automation results) has a
 * shape to feed rather than inventing one alongside the feature.
 *
 * Deliberately *not* persisted. Notifications are server state: once an API
 * exists they belong in React Query, and this store's job narrows to
 * client-side ephemera such as toasts. Persisting them now would create a stale
 * local copy that competes with the server — the exact failure mode
 * `ui-store.ts` warns about.
 */

export type NotificationLevel = "info" | "success" | "warning" | "error";

export interface AppNotification {
  id: string;
  title: string;
  body?: string;
  level: NotificationLevel;
  read: boolean;
  /** ISO 8601. */
  createdAt: string;
  /** Optional in-app destination, e.g. the order that failed to sync. */
  href?: string;
}

interface NotificationState {
  notifications: AppNotification[];
  unreadCount: () => number;
  add: (notification: Omit<AppNotification, "id" | "read" | "createdAt">) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  remove: (id: string) => void;
  clear: () => void;
}

export const useNotificationStore = create<NotificationState>()((set, get) => ({
  notifications: [],

  // A derived getter rather than a stored counter: a second field holding the
  // same fact would eventually disagree with the list it counts.
  unreadCount: () => get().notifications.filter((item) => !item.read).length,

  add: (notification) =>
    set((state) => ({
      notifications: [
        {
          ...notification,
          id:
            typeof crypto !== "undefined" && "randomUUID" in crypto
              ? crypto.randomUUID()
              : `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          read: false,
          createdAt: new Date().toISOString(),
        },
        ...state.notifications,
      ],
    })),

  markRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((item) =>
        item.id === id ? { ...item, read: true } : item,
      ),
    })),

  markAllRead: () =>
    set((state) => ({
      notifications: state.notifications.map((item) => ({ ...item, read: true })),
    })),

  remove: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((item) => item.id !== id),
    })),

  clear: () => set({ notifications: [] }),
}));
