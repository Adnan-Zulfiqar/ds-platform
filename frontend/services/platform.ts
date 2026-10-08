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

/** Track E5d / D-018: platform-operator data access. Never uses the tenant client. */

export type PlatformRole =
  "super_admin" | "admin" | "support" | "finance" | "operations" | "auditor";

export const PLATFORM_ROLES: { value: PlatformRole; label: string }[] = [
  { value: "super_admin", label: "Super admin" },
  { value: "admin", label: "Admin" },
  { value: "support", label: "Support" },
  { value: "finance", label: "Finance" },
  { value: "operations", label: "Operations" },
  { value: "auditor", label: "Auditor" },
];

export function roleLabel(role: string): string {
  return PLATFORM_ROLES.find((r) => r.value === role)?.label ?? role;
}

/** Mirrors `PlatformPermission` on the server, which is what enforces it. */
export type PlatformPermission =
  | "tenants.read"
  | "tenants.suspend"
  | "operators.read"
  | "operators.manage"
  | "audit.read";

export interface PlatformSession {
  id: string;
  createdAt: string;
  expiresAt: string;
  lastSeenAt: string | null;
  reauthenticatedAt: string | null;
  clientIp: string | null;
  userAgent: string | null;
  current: boolean;
}

export interface PlatformMe {
  id: string;
  email: string;
  role: string;
  permissions: string[];
  lastLoginAt: string | null;
  session: PlatformSession;
  reauthValidUntil: string | null;
}

export interface PlatformOperator {
  id: string;
  email: string;
  role: string;
  isActive: boolean;
  createdAt: string;
  lastLoginAt: string | null;
  openSessions: number;
}

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
  actorRole: string | null;
  action: string;
  outcome: string;
  targetTenantId: string | null;
  targetType: string | null;
  targetId: string | null;
  detail: Record<string, unknown>;
  clientIp: string | null;
  userAgent: string | null;
  requestId: string | null;
}

export const platformKeys = {
  all: ["platform"] as const,
  me: () => [...platformKeys.all, "me"] as const,
  sessions: () => [...platformKeys.all, "sessions"] as const,
  operators: () => [...platformKeys.all, "operators"] as const,
  tenants: (q: string, page: number) =>
    [...platformKeys.all, "tenants", q, page] as const,
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
    mutationFn: async (payload: {
      email: string;
      password: string;
      code: string;
    }) => {
      const { data } = await platformClient.post<{
        accessToken: string;
        expiresAt: string;
      }>("/auth/login", payload);
      return data;
    },
    onSuccess: (data) => {
      queryClient.removeQueries({ queryKey: platformKeys.all });
      setPlatformToken(data.accessToken);
    },
  });
}

/** Ends the session on the server too, so the token is dead even if copied. */
export function usePlatformLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await platformClient.post("/auth/logout");
    },
    onSettled: () => {
      setPlatformToken(null);
      queryClient.removeQueries({ queryKey: platformKeys.all });
    },
  });
}

export function usePlatformMe(): UseQueryResult<PlatformMe> {
  return useQuery({
    queryKey: platformKeys.me(),
    queryFn: async () => (await platformClient.get<PlatformMe>("/me")).data,
  });
}

export function usePlatformReauth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { password: string; code: string }) => {
      const { data } = await platformClient.post<{
        reauthenticatedAt: string;
        validUntil: string;
      }>("/auth/reauth", payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: platformKeys.me() });
    },
  });
}

export function usePlatformSessions(): UseQueryResult<PlatformSession[]> {
  return useQuery({
    queryKey: platformKeys.sessions(),
    queryFn: async () =>
      (await platformClient.get<PlatformSession[]>("/auth/sessions")).data,
  });
}

export function useRevokePlatformSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (sessionId: string) => {
      await platformClient.post(`/auth/sessions/${sessionId}/revoke`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: platformKeys.sessions() });
    },
  });
}

export function usePlatformOperators(
  enabled: boolean,
): UseQueryResult<PlatformOperator[]> {
  return useQuery({
    queryKey: platformKeys.operators(),
    queryFn: async () =>
      (await platformClient.get<PlatformOperator[]>("/operators")).data,
    enabled,
  });
}

export type OperatorAction =
  | { kind: "role"; role: PlatformRole }
  | { kind: "deactivate" | "reactivate" | "revoke-sessions" };

export type OperatorChange = OperatorAction & {
  operatorId: string;
  reason: string;
};

export function useChangeOperator() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (change: OperatorChange) => {
      const base = `/operators/${change.operatorId}`;
      if (change.kind === "role") {
        await platformClient.post(`${base}/role`, {
          role: change.role,
          reason: change.reason,
        });
      } else if (change.kind === "revoke-sessions") {
        await platformClient.post(`${base}/sessions/revoke`, {
          reason: change.reason,
        });
      } else {
        await platformClient.post(`${base}/${change.kind}`, {
          reason: change.reason,
        });
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: platformKeys.operators(),
      });
      void queryClient.invalidateQueries({ queryKey: platformKeys.audit() });
    },
  });
}

export function usePlatformTenants(
  q: string,
  page: number,
  enabled: boolean,
): UseQueryResult<Page<PlatformTenant>> {
  return useQuery({
    queryKey: platformKeys.tenants(q, page),
    queryFn: async () => {
      const { data } = await platformClient.get<Page<PlatformTenant>>(
        "/tenants",
        {
          params: { q: q || undefined, page, size: 25 },
        },
      );
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
    mutationFn: async (input: {
      tenantId: string;
      active: boolean;
      reason: string;
    }) => {
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

export function usePlatformAudit(
  enabled: boolean,
): UseQueryResult<PlatformAuditEntry[]> {
  return useQuery({
    queryKey: platformKeys.audit(),
    queryFn: async () => {
      const { data } = await platformClient.get<PlatformAuditEntry[]>(
        "/audit",
        {
          params: { limit: 100 },
        },
      );
      return data;
    },
    enabled,
  });
}

export function usePlatformOperatorSessions(
  operatorId: string,
): UseQueryResult<PlatformSession[]> {
  return useQuery({
    queryKey: [...platformKeys.operators(), operatorId, "sessions"],
    queryFn: async () =>
      (
        await platformClient.get<PlatformSession[]>(
          `/operators/${operatorId}/sessions`,
        )
      ).data,
  });
}
