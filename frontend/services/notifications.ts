import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

export type NotificationKind =
  | "import_completed"
  | "sync_failed"
  | "inventory_changed"
  | "price_changed"
  | "order_imported"
  | "shipment_updated"
  | "webhook_failure"
  | "task_failure"
  | "automation_completed"
  | "automation_failed"
  | "info";

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  href: string | null;
  isRead: boolean;
  createdAt: string;
  readAt: string | null;
}

export interface UnreadCount {
  unread: number;
}

export const notificationKeys = {
  all: ["notifications"] as const,
  list: (query: ListQuery) => [...notificationKeys.all, "list", query] as const,
  unread: () => [...notificationKeys.all, "unread"] as const,
};

export function useNotifications(
  query: ListQuery = {},
): UseQueryResult<Page<AppNotification>> {
  return useQuery({
    queryKey: notificationKeys.list(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<AppNotification>>("/notifications", {
        params: query,
      });
      return data;
    },
  });
}

export function useUnreadNotificationCount(): UseQueryResult<UnreadCount> {
  return useQuery({
    queryKey: notificationKeys.unread(),
    queryFn: async () => {
      const { data } = await apiClient.get<UnreadCount>("/notifications/unread-count");
      return data;
    },
    refetchInterval: 30_000,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.post<AppNotification>(
        `/notifications/${id}/read`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<UnreadCount>("/notifications/read-all");
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

/** Track E3: which notification kinds the signed-in user gets by email. */
export interface EmailPreferences {
  kinds: NotificationKind[];
  available: NotificationKind[];
}

const emailPreferencesKey = [...notificationKeys.all, "email-preferences"] as const;

export function useEmailPreferences(): UseQueryResult<EmailPreferences> {
  return useQuery({
    queryKey: emailPreferencesKey,
    queryFn: async () => {
      const { data } = await apiClient.get<EmailPreferences>("/notifications/email-preferences");
      return data;
    },
  });
}

export function useSaveEmailPreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (kinds: NotificationKind[]) => {
      const { data } = await apiClient.put<EmailPreferences>("/notifications/email-preferences", {
        kinds,
      });
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(emailPreferencesKey, data);
    },
  });
}
