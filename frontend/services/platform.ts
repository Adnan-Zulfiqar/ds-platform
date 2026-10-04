import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import {
  getPlatformToken,
  platformClient,
  setPlatformToken,
  subscribePlatformToken,
} from "@/lib/platform/client";
import type { Page } from "@/types/api";

/** Track E5d: platform-operator data access. Never uses the tenant client. */

export interface PlatformTenant {
  id: string;
  name: string;
  slug: string;
  status: string;
  isActive: boolean;
  createdAt: string;
  users: number;
  connectedStores: number;
}

export interface PlatformTenantHealth {
  tenantId: string;
  windowHours: number;
  failedOrderSyncs: number;
  failedInventorySyncs: number;
  listingsInError: number;
  failedNotificationEmails: number;
}

export interface PlatformAuditEntry {
  id: string;
  createdAt: string;
  adminId: string | null;
  action: string;
  targetTenantId: string | null;
  detail: Record<string, unknown>;
  clientIp: string | null;
}

export const platformKeys = {
  all: ["platform"] as const,
  tenants: (q: string, page: number) => [...platformKeys.all, "tenants", q, page] as const,
  health: (id: string) => [...platformKeys.all, "health", id] as const,
  audit: () => [...platformKeys.all, "audit"] as const,
};

/** Whether an operator session exists in this tab. */
export function usePlatformSignedIn(): boolean {
  return useSyncExternalStore(
    subscribePlatformToken,
    () => getPlatformToken() !== null,
    () => false,
  );
}

export function usePlatformLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { email: string; password: string; code: string }) => {
      const { data } = await platformClient.post<{ accessToken: string; expiresAt: string }>(
        "/auth/login",
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      queryClient.removeQueries({ queryKey: platformKeys.all });
      setPlatformToken(data.accessToken);
    },
  });
}

export function signOutPlatform(): void {
  setPlatformToken(null);
}

export function usePlatformTenants(
  q: string,
  page: number,
  enabled: boolean,
): UseQueryResult<Page<PlatformTenant>> {
  return useQuery({
    queryKey: platformKeys.tenants(q, page),
    queryFn: async () => {
      const { data } = await platformClient.get<Page<PlatformTenant>>("/tenants", {
        params: { q: q || undefined, page, size: 25 },
      });
      return data;
    },
    enabled,
  });
}

export function usePlatformTenantHealth(
  tenantId: string | null,
): UseQueryResult<PlatformTenantHealth> {
  return useQuery({
    queryKey: platformKeys.health(tenantId ?? ""),
    queryFn: async () => {
      const { data } = await platformClient.get<PlatformTenantHealth>(
        `/tenants/${tenantId}/health`,
      );
      return data;
    },
    enabled: tenantId !== null,
  });
}

export function useSetTenantActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { tenantId: string; active: boolean; reason: string }) => {
      const action = input.active ? "reactivate" : "suspend";
      const { data } = await platformClient.post<PlatformTenant>(
        `/tenants/${input.tenantId}/${action}`,
        { reason: input.reason },
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: platformKeys.all });
    },
  });
}

export function usePlatformAudit(enabled: boolean): UseQueryResult<PlatformAuditEntry[]> {
  return useQuery({
    queryKey: platformKeys.audit(),
    queryFn: async () => {
      const { data } = await platformClient.get<PlatformAuditEntry[]>("/audit", {
        params: { limit: 100 },
      });
      return data;
    },
    enabled,
  });
}
