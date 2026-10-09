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
import { ApiError } from "@/lib/api-client";
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
  | "audit.read"
  | "audit.export"
  | "dashboard.read"
  | "workspace.data.read"
  | "support.session"
  | "users.manage"
  | "stores.manage"
  | "catalog.manage"
  | "orders.manage"
  | "jobs.read"
  | "jobs.manage"
  | "billing.read"
  | "billing.manage"
  | "settings.manage";

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

export interface DailyCount {
  day: string;
  count: number;
}

export interface PlatformDashboard {
  generatedAt: string;
  tenantsByStatus: Record<string, number>;
  tenantsNewWeek: number;
  tenantsNewMonth: number;
  usersActive: number;
  usersNewWeek: number;
  storesByStatus: Record<string, number>;
  storesByPlatform: Record<string, number>;
  productsTotal: number;
  listingsByStatus: Record<string, number>;
  ordersLastDay: number;
  ordersLastWeek: number;
  subscriptionsByPlan: Record<string, number>;
  subscriptionsByStatus: Record<string, number>;
  trialsEndingWeek: number;
  failedLastDay: Record<string, number>;
  stuck: Record<string, number>;
  operatorSessionsOpen: number;
  securityFailuresLastDay: number;
  signupsByDay: DailyCount[];
  ordersByDay: DailyCount[];
  system: {
    database: boolean;
    redis: boolean;
    migrationRevision: string | null;
  };
}

export function usePlatformDashboard(
  enabled: boolean,
): UseQueryResult<PlatformDashboard> {
  return useQuery({
    queryKey: [...platformKeys.all, "dashboard"],
    queryFn: async () =>
      (await platformClient.get<PlatformDashboard>("/dashboard")).data,
    enabled,
    // Live, but not a firehose: a minute is fresh enough for counts.
    refetchInterval: 60_000,
  });
}

export interface PlatformWorkspaceOverview {
  id: string;
  name: string;
  slug: string;
  status: string;
  isActive: boolean;
  timezone: string | null;
  defaultCurrency: string | null;
  createdAt: string;
  users: number;
  activeUsers: number;
  storesByStatus: Record<string, number>;
  products: Record<string, number>;
  ordersByStatus: Record<string, number>;
  listingsByStatus: Record<string, number>;
  subscription: {
    plan: string | null;
    status: string;
    aiAddon: boolean;
    trialEndsAt: string;
    currentPeriodEnd: string | null;
    cancelAtPeriodEnd: boolean;
    hasStripeCustomer: boolean;
  } | null;
  health: {
    windowHours: number;
    failedOrderSyncs: number;
    failedInventorySyncs: number;
    listingsInError: number;
    failedNotificationEmails: number;
  };
}

export function usePlatformWorkspace(
  tenantId: string,
  enabled: boolean,
): UseQueryResult<PlatformWorkspaceOverview> {
  return useQuery({
    queryKey: [...platformKeys.all, "workspace", tenantId],
    queryFn: async () =>
      (
        await platformClient.get<PlatformWorkspaceOverview>(
          `/workspaces/${tenantId}`,
        )
      ).data,
    enabled,
  });
}

// --- Workspace drill-down (phase 3) ------------------------------------------

export type WorkspaceResource =
  | "users"
  | "invitations"
  | "stores"
  | "products"
  | "listings"
  | "orders"
  | "sync-runs"
  | "imports"
  | "notifications";

/** Query parameters for one workspace list. Empty values are dropped. */
export type WorkspaceListParams = Record<
  string,
  string | number | boolean | undefined
>;

/** One page of any workspace list. Each view is audited on the server. */
export function usePlatformWorkspaceList<T>(
  tenantId: string,
  resource: WorkspaceResource,
  params: WorkspaceListParams,
): UseQueryResult<Page<T>> {
  return useQuery({
    queryKey: [...platformKeys.all, "workspace", tenantId, resource, params],
    queryFn: async () => {
      const clean = Object.fromEntries(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
      );
      return (
        await platformClient.get<Page<T>>(
          `/workspaces/${tenantId}/${resource}`,
          { params: clean },
        )
      ).data;
    },
    placeholderData: (previous) => previous,
  });
}

export interface WorkspaceConnection {
  kind: string;
  id: string;
  status: string;
  label: string | null;
  storeId: string | null;
  lastSyncAt: string | null;
  tokenExpiresAt: string | null;
  webhooksRegisteredAt: string | null;
  lastError: string | null;
  createdAt: string;
}

export function usePlatformWorkspaceConnections(
  tenantId: string,
): UseQueryResult<WorkspaceConnection[]> {
  return useQuery({
    queryKey: [...platformKeys.all, "workspace", tenantId, "connections"],
    queryFn: async () =>
      (
        await platformClient.get<WorkspaceConnection[]>(
          `/workspaces/${tenantId}/connections`,
        )
      ).data,
  });
}

/** A record in a workspace, by kind and id. */
export function usePlatformWorkspaceRecord<T>(
  tenantId: string,
  kind: "orders" | "products",
  id: string | null,
): UseQueryResult<T> {
  return useQuery({
    queryKey: [...platformKeys.all, "workspace", tenantId, kind, "detail", id],
    queryFn: async () =>
      (await platformClient.get<T>(`/workspaces/${tenantId}/${kind}/${id}`))
        .data,
    enabled: id !== null,
  });
}

export const EXPORT_DATASETS = [
  { value: "users", label: "Users" },
  { value: "stores", label: "Stores" },
  { value: "products", label: "Products" },
  { value: "drafts", label: "Drafts" },
  { value: "listings", label: "Listings" },
  { value: "orders", label: "Orders" },
] as const;

/**
 * Downloads one dataset as CSV (re-auth, audited, at most 5000 rows).
 *
 * The body is a file, so an error body arrives as a Blob too; it is read and
 * rethrown as the usual `ApiError`, which is what lets the re-auth prompt see
 * `reauth_required` here as everywhere else.
 */
export async function downloadWorkspaceExport(
  tenantId: string,
  dataset: string,
): Promise<void> {
  const response = await platformClient.get<Blob>(
    `/workspaces/${tenantId}/export/${dataset}`,
    {
      responseType: "blob",
      validateStatus: () => true,
    },
  );
  if (response.status !== 200) {
    if (response.status === 401) setPlatformToken(null);
    let body: { code?: string; message?: string } = {};
    try {
      body = JSON.parse(await response.data.text()) as typeof body;
    } catch {
      // not JSON: fall through to the generic error
    }
    throw new ApiError({
      code: body.code ?? "export_failed",
      message: body.message ?? "The export failed.",
      status: response.status,
    });
  }
  const disposition = String(response.headers["content-disposition"] ?? "");
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${dataset}.csv`;
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

// --- Support sessions and workspace changes (phase 4) -------------------------

export interface SupportSession {
  id: string;
  reason: string;
  createdAt: string;
  expiresAt: string;
  endedAt: string | null;
}

const supportKey = (tenantId: string) =>
  [...platformKeys.all, "workspace", tenantId, "support-session"] as const;

/** This operator's open support session for the workspace, or null. */
export function useSupportSession(
  tenantId: string,
): UseQueryResult<SupportSession | null> {
  return useQuery({
    queryKey: supportKey(tenantId),
    queryFn: async () =>
      (
        await platformClient.get<SupportSession | null>(
          `/workspaces/${tenantId}/support-session`,
        )
      ).data,
    // Expiry is server time; re-ask often enough that the banner is honest.
    refetchInterval: 60_000,
  });
}

export function useOpenSupportSession(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { reason: string; minutes: number }) =>
      (
        await platformClient.post<SupportSession>(
          `/workspaces/${tenantId}/support-session`,
          input,
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: supportKey(tenantId) });
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "workspace", tenantId, "notifications"],
      });
    },
  });
}

export function useEndSupportSession(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await platformClient.post(`/workspaces/${tenantId}/support-session/end`);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: supportKey(tenantId) }),
  });
}

export type WorkspaceUserAction =
  | { kind: "disable" | "enable" | "end-sessions" | "require-password-reset" }
  | { kind: "role"; role: "admin" | "member" | "viewer" };

/** A change to one workspace user (needs a support session and re-auth). */
export function useWorkspaceUserAction(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      userId: string;
      reason: string;
      action: WorkspaceUserAction;
    }) => {
      const base = `/workspaces/${tenantId}/users/${input.userId}`;
      const { action } = input;
      if (action.kind === "role") {
        await platformClient.post(`${base}/role`, {
          role: action.role,
          reason: input.reason,
        });
      } else {
        await platformClient.post(`${base}/${action.kind}`, {
          reason: input.reason,
        });
      }
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "workspace", tenantId],
      }),
  });
}

export function useRevokeWorkspaceInvitation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { invitationId: string; reason: string }) => {
      await platformClient.post(
        `/workspaces/${tenantId}/invitations/${input.invitationId}/revoke`,
        { reason: input.reason },
      );
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "workspace", tenantId, "invitations"],
      }),
  });
}

// --- Store and integration changes (phase 5) ----------------------------------

export type WorkspaceStoreAction =
  | { kind: "pause" | "resume"; storeId: string }
  | { kind: "shopify-webhooks"; storeId: string }
  | { kind: "sync-orders" }
  | { kind: "sync-inventory"; storeId: string | null };

/** A store or integration change (support session, re-auth, audited). */
export function useWorkspaceStoreAction(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      reason: string;
      action: WorkspaceStoreAction;
    }) => {
      const base = `/workspaces/${tenantId}`;
      const { action, reason } = input;
      switch (action.kind) {
        case "pause":
        case "resume":
          return (
            await platformClient.post(
              `${base}/stores/${action.storeId}/${action.kind}`,
              { reason },
            )
          ).data as unknown;
        case "shopify-webhooks":
          return (
            await platformClient.post(
              `${base}/stores/${action.storeId}/shopify/webhooks`,
              { reason },
            )
          ).data as unknown;
        case "sync-orders":
          return (await platformClient.post(`${base}/sync/orders`, { reason }))
            .data as unknown;
        case "sync-inventory":
          return (
            await platformClient.post(`${base}/sync/inventory`, {
              reason,
              storeId: action.storeId ?? undefined,
            })
          ).data as unknown;
      }
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "workspace", tenantId],
      }),
  });
}

// --- Catalogue and orders (phase 6) -------------------------------------------

export type WorkspaceCatalogAction =
  | { kind: "retry-import"; importId: string }
  | { kind: "resync-listings"; productId: string }
  | { kind: "refresh-order"; orderId: string }
  | { kind: "release-supplier-order"; orderId: string };

/** A catalogue or order change (support session, re-auth, audited). */
export function useWorkspaceCatalogAction(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      reason: string;
      action: WorkspaceCatalogAction;
    }) => {
      const base = `/workspaces/${tenantId}`;
      const { action, reason } = input;
      const path =
        action.kind === "retry-import"
          ? `${base}/imports/${action.importId}/retry`
          : action.kind === "resync-listings"
            ? `${base}/products/${action.productId}/resync-listings`
            : action.kind === "refresh-order"
              ? `${base}/orders/${action.orderId}/refresh`
              : `${base}/orders/${action.orderId}/supplier-order/release`;
      return (await platformClient.post(path, { reason })).data as unknown;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "workspace", tenantId],
      }),
  });
}

// --- Jobs (phase 7) -------------------------------------------------------------

export type JobKind =
  | "order_sync"
  | "inventory_sync"
  | "product_import"
  | "pipeline_run"
  | "rule_application"
  | "supplier_order"
  | "automation_run";

export interface PlatformJob {
  kind: JobKind;
  id: string;
  tenantId: string;
  tenantName: string;
  status: string;
  error: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

export function usePlatformJobsSummary(): UseQueryResult<
  Record<string, Record<string, number>>
> {
  return useQuery({
    queryKey: [...platformKeys.all, "jobs", "summary"],
    queryFn: async () =>
      (
        await platformClient.get<Record<string, Record<string, number>>>(
          "/jobs/summary",
        )
      ).data,
    refetchInterval: 60_000,
  });
}

export function usePlatformJobs(
  kind: JobKind,
  state: "failed" | "stuck",
  page: number,
): UseQueryResult<Page<PlatformJob>> {
  return useQuery({
    queryKey: [...platformKeys.all, "jobs", kind, state, page],
    queryFn: async () =>
      (
        await platformClient.get<Page<PlatformJob>>("/jobs", {
          params: { kind, state, page, size: 25 },
        })
      ).data,
    placeholderData: (previous) => previous,
  });
}

/** One action on one job, in that job's workspace (support session, re-auth). */
export function useJobAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { job: PlatformJob; reason: string }) => {
      const { job, reason } = input;
      const base = `/workspaces/${job.tenantId}/jobs/${job.kind}/${job.id}`;
      const verb =
        job.kind === "order_sync" || job.kind === "inventory_sync"
          ? "close"
          : job.kind === "automation_run"
            ? "retry"
            : "cancel";
      return (await platformClient.post(`${base}/${verb}`, { reason }))
        .data as unknown;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: [...platformKeys.all, "jobs"],
      }),
  });
}
