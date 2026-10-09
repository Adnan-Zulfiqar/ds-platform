"use client";

import { useState } from "react";

import {
  type Column,
  PlatformDataTable,
} from "@/components/platform/platform-data-table";
import { usePlatformAccess } from "@/components/platform/platform-shell";
import {
  ReasonedAction,
  describeError,
} from "@/components/platform/platform-workspace-actions";
import { useReauthGuard } from "@/components/platform/reauth-dialog";
import { Facts } from "@/components/platform/platform-workspaces";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import {
  EXPORT_DATASETS,
  type WorkspaceUserAction,
  downloadWorkspaceExport,
  usePlatformWorkspaceConnections,
  usePlatformWorkspaceRecord,
  useRevokeWorkspaceInvitation,
  useWorkspaceStoreAction,
  useWorkspaceUserAction,
} from "@/services/platform";

/** Admin Control Center phase 3 (D-019): everything inside one workspace,
 * read through its tenant-scoped API, one audited view at a time. */

const when = (v: string | null | undefined) => (v ? formatDateTime(v) : "—");
const money = (amount: string | number | null, currency: string | null) =>
  amount === null ? "—" : `${amount} ${currency ?? ""}`.trim();
const opts = (...values: string[]) =>
  values.map((v) => ({ value: v, label: v.replaceAll("_", " ") }));

function StatusBadge({ value, bad = [] }: { value: string; bad?: string[] }) {
  return (
    <Badge variant={bad.includes(value) ? "destructive" : "outline"}>
      {value}
    </Badge>
  );
}

interface UserRow {
  id: string;
  email: string;
  firstName: string | null;
  lastName: string | null;
  isActive: boolean;
  isVerified: boolean;
  lastLoginAt: string | null;
  createdAt: string;
  roles: string[];
  activeSessions: number;
}

interface InvitationRow {
  id: string;
  email: string;
  role: string;
  createdAt: string;
  expiresAt: string;
  acceptedAt: string | null;
  revokedAt: string | null;
}

interface StoreRow {
  id: string;
  name: string;
  platform: string;
  status: string;
  currency: string;
  inventorySyncEnabled: boolean;
  pricingSyncEnabled: boolean;
  orderSyncEnabled: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  healthScore: number;
  syncPausedAt: string | null;
  syncPausedReason: string | null;
}

interface ProductRow {
  id: string;
  title: string;
  status: string;
  source: string;
  supplierName: string | null;
  currency: string | null;
  sellPrice: string | null;
  stockQuantity: number;
  aiStatus: string;
  variantCount: number;
  lastSyncError: string | null;
  updatedAt: string;
}

interface ListingRow {
  id: string;
  productId: string;
  storeId: string;
  status: string;
  shopDomain: string | null;
  publishedAt: string | null;
  lastSyncedAt: string | null;
  lastError: string | null;
}

interface OrderRow {
  id: string;
  source: string;
  externalId: string;
  fulfillmentStatus: string;
  paymentStatus: string;
  buyerName: string | null;
  buyerCountry: string | null;
  currency: string | null;
  totalAmount: string | null;
  externalCreatedAt: string | null;
  lastSyncError: string | null;
  createdAt: string;
}

interface RunRow {
  id: string;
  kind: string;
  status: string;
  trigger: string;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

interface NotificationRow {
  id: string;
  kind: string;
  title: string;
  body: string;
  isRead: boolean;
  emailStatus: string | null;
  createdAt: string;
}

export function UsersTab({ tenantId }: { tenantId: string }) {
  const { can } = usePlatformAccess();
  const [open, setOpen] = useState<UserRow | null>(null);
  const revoke = useRevokeWorkspaceInvitation(tenantId);
  const columns: Column<UserRow>[] = [
    {
      header: "User",
      cell: (u) => (
        <div>
          <div className="font-medium">{u.email}</div>
          <div className="text-xs text-muted-foreground">
            {[u.firstName, u.lastName].filter(Boolean).join(" ") || "—"}
          </div>
        </div>
      ),
    },
    { header: "Roles", cell: (u) => u.roles.join(", ") || "—" },
    {
      header: "State",
      cell: (u) => (
        <div className="flex gap-1">
          <Badge variant={u.isActive ? "outline" : "destructive"}>
            {u.isActive ? "active" : "disabled"}
          </Badge>
          {!u.isVerified && <Badge variant="outline">unverified</Badge>}
        </div>
      ),
    },
    { header: "Sessions", cell: (u) => u.activeSessions },
    { header: "Last sign-in", cell: (u) => when(u.lastLoginAt) },
    { header: "Joined", cell: (u) => when(u.createdAt) },
  ];
  const invitationColumns: Column<InvitationRow>[] = [
    { header: "Email", cell: (i) => i.email },
    { header: "Role", cell: (i) => i.role },
    {
      header: "State",
      cell: (i) =>
        i.acceptedAt
          ? "accepted"
          : i.revokedAt
            ? "revoked"
            : `expires ${when(i.expiresAt)}`,
    },
    { header: "Sent", cell: (i) => when(i.createdAt) },
    ...(can("users.manage")
      ? [
          {
            header: "",
            cell: (i: InvitationRow) =>
              i.acceptedAt || i.revokedAt ? null : (
                <InvitationRevoke
                  onRevoke={(reason) =>
                    revoke.mutateAsync({ invitationId: i.id, reason })
                  }
                />
              ),
          },
        ]
      : []),
  ];
  return (
    <div className="space-y-6">
      <PlatformDataTable<UserRow>
        tenantId={tenantId}
        resource="users"
        columns={columns}
        filters={[
          {
            param: "active",
            label: "State",
            options: [
              { value: "true", label: "active" },
              { value: "false", label: "disabled" },
            ],
          },
        ]}
        onRowClick={setOpen}
        emptyText="No users match."
        testId="platform-workspace-users"
      />
      <div>
        <h2 className="mb-2 text-base font-semibold">Invitations</h2>
        <PlatformDataTable<InvitationRow>
          tenantId={tenantId}
          resource="invitations"
          columns={invitationColumns}
          searchable={false}
          emptyText="No invitations."
        />
      </div>
      <UserSheet
        tenantId={tenantId}
        user={open}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}

function InvitationRevoke({
  onRevoke,
}: {
  onRevoke: (reason: string) => Promise<unknown>;
}) {
  const guard = useReauthGuard();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          const reason =
            window.prompt("Reason for revoking this invitation (audited):") ??
            "";
          if (reason.trim().length < 3) return;
          setError(null);
          guard(() => onRevoke(reason.trim())).catch((e: unknown) =>
            setError(describeError(e)),
          );
        }}
      >
        Revoke
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}

function UserSheet({
  tenantId,
  user,
  onClose,
}: {
  tenantId: string;
  user: UserRow | null;
  onClose: () => void;
}) {
  const { can } = usePlatformAccess();
  const act = useWorkspaceUserAction(tenantId);
  const [role, setRole] = useState<"admin" | "member" | "viewer">("member");
  const isOwner = user?.roles.includes("owner") ?? false;
  const run = (action: WorkspaceUserAction) => (reason: string) =>
    act.mutateAsync({ userId: user?.id ?? "", reason, action });

  return (
    <Sheet open={user !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{user?.email}</SheetTitle>
          <SheetDescription>
            {user?.roles.join(", ")} · {user?.isActive ? "active" : "disabled"}{" "}
            · {user?.activeSessions} session(s)
          </SheetDescription>
        </SheetHeader>
        {user && (
          <div className="mt-4 space-y-3" data-testid="platform-user-actions">
            {!can("users.manage") ? (
              <p className="text-sm text-muted-foreground">
                Your role cannot change users.
              </p>
            ) : (
              <>
                <ReasonedAction
                  label={user.isActive ? "Disable user" : "Enable user"}
                  description={
                    user.isActive
                      ? "They are signed out and cannot sign in. The only active owner cannot be disabled."
                      : "They can sign in again."
                  }
                  destructive={user.isActive}
                  onRun={run({ kind: user.isActive ? "disable" : "enable" })}
                />
                <ReasonedAction
                  label="End all sessions"
                  description="Signs them out on every device."
                  onRun={run({ kind: "end-sessions" })}
                />
                <ReasonedAction
                  label="Require password reset"
                  description='The current password stops working and they are signed out. They set a new one with "Forgot password".'
                  destructive
                  onRun={run({ kind: "require-password-reset" })}
                />
                {isOwner ? (
                  <p className="text-sm text-muted-foreground">
                    An owner&apos;s role is not changed from the console.
                  </p>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="platform-member-role">
                      New workspace role
                    </Label>
                    <select
                      id="platform-member-role"
                      className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                      value={role}
                      onChange={(e) =>
                        setRole(e.target.value as "admin" | "member" | "viewer")
                      }
                    >
                      <option value="admin">admin</option>
                      <option value="member">member</option>
                      <option value="viewer">viewer</option>
                    </select>
                    <ReasonedAction
                      label="Change role"
                      description="Takes effect now: their sessions end."
                      onRun={run({ kind: "role", role })}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function StoresTab({ tenantId }: { tenantId: string }) {
  const { can } = usePlatformAccess();
  const [open, setOpen] = useState<StoreRow | null>(null);
  const connections = usePlatformWorkspaceConnections(tenantId);
  const columns: Column<StoreRow>[] = [
    {
      header: "Store",
      cell: (s) => <span className="font-medium">{s.name}</span>,
    },
    { header: "Platform", cell: (s) => s.platform },
    {
      header: "Status",
      cell: (s) => (
        <div className="flex gap-1">
          <StatusBadge value={s.status} bad={["error", "disconnected"]} />
          {s.syncPausedAt && <Badge variant="destructive">paused</Badge>}
        </div>
      ),
    },
    {
      header: "Sync",
      cell: (s) =>
        [
          s.orderSyncEnabled && "orders",
          s.inventorySyncEnabled && "inventory",
          s.pricingSyncEnabled && "pricing",
        ]
          .filter(Boolean)
          .join(", ") || "paused",
    },
    { header: "Last sync", cell: (s) => when(s.lastSyncAt) },
    { header: "Health", cell: (s) => s.healthScore },
    {
      header: "Last error",
      cell: (s) => (
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {s.lastError ?? "—"}
        </span>
      ),
    },
  ];
  return (
    <div className="space-y-6">
      <PlatformDataTable<StoreRow>
        tenantId={tenantId}
        resource="stores"
        columns={columns}
        filters={[
          {
            param: "platform",
            label: "Platform",
            options: opts(
              "shopify",
              "woocommerce",
              "ebay",
              "etsy",
              "tiktok_shop",
              "manual",
            ),
          },
          {
            param: "status",
            label: "Status",
            options: opts(
              "pending",
              "connected",
              "disconnected",
              "error",
              "syncing",
            ),
          },
        ]}
        onRowClick={setOpen}
        emptyText="No stores."
        testId="platform-workspace-stores"
      />
      {can("stores.manage") && <SyncNow tenantId={tenantId} />}
      <StoreSheet
        tenantId={tenantId}
        store={open}
        onClose={() => setOpen(null)}
      />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Connections</CardTitle>
          <CardDescription>
            Supplier and channel accounts. Credentials are never shown.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {connections.isError ? (
            <ErrorState
              title="Could not load connections"
              onRetry={() => void connections.refetch()}
            />
          ) : !connections.data ? (
            <Skeleton className="h-20 w-full" />
          ) : connections.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No supplier or channel connections.
            </p>
          ) : (
            <ul
              className="divide-y text-sm"
              data-testid="platform-workspace-connections"
            >
              {connections.data.map((c) => (
                <li
                  key={c.id}
                  className="flex flex-wrap items-center gap-3 py-2"
                >
                  <span className="w-24 font-medium capitalize">{c.kind}</span>
                  <StatusBadge
                    value={c.status}
                    bad={["error", "expired", "reconnect_required"]}
                  />
                  <span className="text-muted-foreground">{c.label ?? ""}</span>
                  <span className="text-muted-foreground">
                    last sync {when(c.lastSyncAt)}
                  </span>
                  {c.tokenExpiresAt && (
                    <span className="text-muted-foreground">
                      access until {when(c.tokenExpiresAt)}
                    </span>
                  )}
                  {c.kind === "shopify" && (
                    <span className="text-muted-foreground">
                      webhooks{" "}
                      {c.webhooksRegisteredAt
                        ? when(c.webhooksRegisteredAt)
                        : "not registered"}
                    </span>
                  )}
                  {c.lastError && (
                    <span className="w-full text-xs text-destructive">
                      {c.lastError}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SyncNow({ tenantId }: { tenantId: string }) {
  const act = useWorkspaceStoreAction(tenantId);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Run a sync now</CardTitle>
        <CardDescription>
          The same syncs the merchant can start. A sync already running is
          refused, as for them.
        </CardDescription>
      </CardHeader>
      <CardContent
        className="grid gap-3 md:grid-cols-2"
        data-testid="platform-sync-now"
      >
        <ReasonedAction
          label="Sync supplier orders"
          onRun={(reason) =>
            act.mutateAsync({ reason, action: { kind: "sync-orders" } })
          }
        />
        <ReasonedAction
          label="Sync inventory"
          description="All stores. Changed stock is pushed to every store that is not paused."
          onRun={(reason) =>
            act.mutateAsync({
              reason,
              action: { kind: "sync-inventory", storeId: null },
            })
          }
        />
      </CardContent>
    </Card>
  );
}

function StoreSheet({
  tenantId,
  store,
  onClose,
}: {
  tenantId: string;
  store: StoreRow | null;
  onClose: () => void;
}) {
  const { can } = usePlatformAccess();
  const act = useWorkspaceStoreAction(tenantId);
  return (
    <Sheet open={store !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{store?.name}</SheetTitle>
          <SheetDescription>
            {store?.platform} · {store?.status}
            {store?.syncPausedAt
              ? ` · paused since ${when(store.syncPausedAt)}`
              : ""}
          </SheetDescription>
        </SheetHeader>
        {store && (
          <div className="mt-4 space-y-3" data-testid="platform-store-actions">
            <Facts
              rows={[
                ["Last sync", when(store.lastSyncAt)],
                ["Health", store.healthScore],
                ["Last error", store.lastError ?? "—"],
                ["Pause reason", store.syncPausedReason ?? "—"],
              ]}
            />
            {!can("stores.manage") ? (
              <p className="text-sm text-muted-foreground">
                Your role cannot change stores.
              </p>
            ) : (
              <>
                <ReasonedAction
                  label={
                    store.syncPausedAt ? "Resume updates" : "Pause updates"
                  }
                  description={
                    store.syncPausedAt
                      ? "Prices, stock and new listings are sent again."
                      : "DropPilot stops sending prices, stock and new listings. Orders keep arriving. The merchant is notified."
                  }
                  destructive={!store.syncPausedAt}
                  onRun={(reason) =>
                    act.mutateAsync({
                      reason,
                      action: {
                        kind: store.syncPausedAt ? "resume" : "pause",
                        storeId: store.id,
                      },
                    })
                  }
                />
                <ReasonedAction
                  label="Sync this store's inventory"
                  onRun={(reason) =>
                    act.mutateAsync({
                      reason,
                      action: { kind: "sync-inventory", storeId: store.id },
                    })
                  }
                />
                {store.platform === "shopify" && (
                  <ReasonedAction
                    label="Re-register Shopify webhooks"
                    description="Creates only what is missing, as on connect."
                    onRun={(reason) =>
                      act.mutateAsync({
                        reason,
                        action: { kind: "shopify-webhooks", storeId: store.id },
                      })
                    }
                  />
                )}
              </>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function ProductsTab({ tenantId }: { tenantId: string }) {
  const [publication, setPublication] = useState<"published" | "draft">(
    "published",
  );
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<ProductRow>[] = [
    {
      header: "Title",
      cell: (p) => <span className="line-clamp-2 font-medium">{p.title}</span>,
    },
    {
      header: "Status",
      cell: (p) => <StatusBadge value={p.status} bad={["unavailable"]} />,
    },
    { header: "Source", cell: (p) => p.source },
    { header: "Price", cell: (p) => money(p.sellPrice, p.currency) },
    { header: "Stock", cell: (p) => p.stockQuantity },
    { header: "Variants", cell: (p) => p.variantCount },
    {
      header: "AI",
      cell: (p) => <StatusBadge value={p.aiStatus} bad={["failed"]} />,
    },
    { header: "Updated", cell: (p) => when(p.updatedAt) },
  ];
  return (
    <div className="space-y-3">
      <div className="flex gap-2" role="group" aria-label="Products or drafts">
        {(["published", "draft"] as const).map((v) => (
          <Button
            key={v}
            size="sm"
            variant={publication === v ? "default" : "outline"}
            aria-pressed={publication === v}
            onClick={() => setPublication(v)}
          >
            {v === "published" ? "Products" : "Drafts"}
          </Button>
        ))}
      </div>
      <PlatformDataTable<ProductRow>
        key={publication}
        tenantId={tenantId}
        resource="products"
        fixed={{ publication }}
        columns={columns}
        onRowClick={(p) => setOpen(p.id)}
        emptyText={
          publication === "draft" ? "No drafts." : "No published products."
        }
        testId="platform-workspace-products"
      />
      <ProductSheet
        tenantId={tenantId}
        id={open}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}

interface ProductDetail extends ProductRow {
  description: string | null;
  externalUrl: string | null;
  categoryName: string | null;
  tags: string[];
  images: string[];
  variants: {
    id: string;
    label: string | null;
    merchantSku: string | null;
    costPrice: string | null;
    sellPrice: string | null;
    currency: string | null;
    stockQuantity: number;
    isEnabled: boolean;
  }[];
  listings: ListingRow[];
}

function ProductSheet({
  tenantId,
  id,
  onClose,
}: {
  tenantId: string;
  id: string | null;
  onClose: () => void;
}) {
  const detail = usePlatformWorkspaceRecord<ProductDetail>(
    tenantId,
    "products",
    id,
  );
  return (
    <Sheet open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{detail.data?.title ?? "Product"}</SheetTitle>
          <SheetDescription>Read-only. Opening it is audited.</SheetDescription>
        </SheetHeader>
        {detail.isError ? (
          <ErrorState
            title="Could not load the product"
            onRetry={() => void detail.refetch()}
          />
        ) : !detail.data ? (
          <Skeleton className="mt-4 h-64 w-full" />
        ) : (
          <div
            className="mt-4 space-y-4 text-sm"
            data-testid="platform-product-detail"
          >
            <Facts
              rows={[
                ["Status", detail.data.status],
                ["Source", detail.data.source],
                ["Supplier", detail.data.supplierName ?? "—"],
                ["Category", detail.data.categoryName ?? "—"],
                ["Price", money(detail.data.sellPrice, detail.data.currency)],
                ["Stock", detail.data.stockQuantity],
                ["Last sync error", detail.data.lastSyncError ?? "—"],
              ]}
            />
            {detail.data.images.length > 0 && (
              <div className="flex gap-2 overflow-x-auto">
                {detail.data.images.slice(0, 8).map((src) => (
                  // eslint-disable-next-line @next/next/no-img-element -- supplier CDNs vary; no optimiser config for them
                  <img
                    key={src}
                    src={src}
                    alt=""
                    className="h-20 w-20 rounded border object-cover"
                  />
                ))}
              </div>
            )}
            <div>
              <p className="mb-1 font-medium">
                Variants ({detail.data.variants.length})
              </p>
              <ul className="divide-y rounded border">
                {detail.data.variants.map((v) => (
                  <li key={v.id} className="flex justify-between gap-2 p-2">
                    <span>{v.label ?? v.merchantSku ?? "variant"}</span>
                    <span className="text-muted-foreground">
                      {money(v.sellPrice, v.currency)} · stock {v.stockQuantity}
                      {!v.isEnabled && " · disabled"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1 font-medium">
                Listings ({detail.data.listings.length})
              </p>
              <ul className="divide-y rounded border">
                {detail.data.listings.map((l) => (
                  <li key={l.id} className="p-2">
                    <StatusBadge value={l.status} bad={["error"]} />{" "}
                    {l.shopDomain ?? l.storeId}
                    {l.lastError && (
                      <p className="text-xs text-destructive">{l.lastError}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            {detail.data.description && (
              <div>
                <p className="mb-1 font-medium">Description</p>
                <p className="whitespace-pre-wrap text-muted-foreground">
                  {detail.data.description.slice(0, 2000)}
                </p>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function ListingsTab({ tenantId }: { tenantId: string }) {
  const columns: Column<ListingRow>[] = [
    {
      header: "Status",
      cell: (l) => <StatusBadge value={l.status} bad={["error"]} />,
    },
    { header: "Shop", cell: (l) => l.shopDomain ?? l.storeId.slice(0, 8) },
    { header: "Product", cell: (l) => l.productId.slice(0, 8) },
    { header: "Published", cell: (l) => when(l.publishedAt) },
    { header: "Last sync", cell: (l) => when(l.lastSyncedAt) },
    {
      header: "Last error",
      cell: (l) => (
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {l.lastError ?? "—"}
        </span>
      ),
    },
  ];
  return (
    <PlatformDataTable<ListingRow>
      tenantId={tenantId}
      resource="listings"
      columns={columns}
      filters={[
        {
          param: "status",
          label: "Status",
          options: opts("pending", "synced", "error", "removed"),
        },
      ]}
      emptyText="No listings."
      testId="platform-workspace-listings"
    />
  );
}

export function OrdersTab({ tenantId }: { tenantId: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<OrderRow>[] = [
    {
      header: "Order",
      cell: (o) => <span className="font-medium">{o.externalId}</span>,
    },
    { header: "Source", cell: (o) => o.source },
    {
      header: "Buyer",
      cell: (o) =>
        `${o.buyerName ?? "—"} ${o.buyerCountry ? `(${o.buyerCountry})` : ""}`,
    },
    { header: "Total", cell: (o) => money(o.totalAmount, o.currency) },
    {
      header: "Fulfilment",
      cell: (o) => (
        <StatusBadge
          value={o.fulfillmentStatus}
          bad={["cancelled", "refunded", "disputed"]}
        />
      ),
    },
    { header: "Payment", cell: (o) => o.paymentStatus },
    { header: "Placed", cell: (o) => when(o.externalCreatedAt ?? o.createdAt) },
  ];
  return (
    <>
      <PlatformDataTable<OrderRow>
        tenantId={tenantId}
        resource="orders"
        columns={columns}
        filters={[
          {
            param: "fulfillment_status",
            label: "Fulfilment",
            options: opts(
              "pending",
              "awaiting_payment",
              "paid",
              "processing",
              "fulfilled",
              "shipped",
              "delivered",
              "cancelled",
              "refunded",
              "disputed",
            ),
          },
          {
            param: "source",
            label: "Source",
            options: opts(
              "shopify",
              "ebay",
              "woocommerce",
              "aliexpress",
              "manual",
            ),
          },
        ]}
        onRowClick={(o) => setOpen(o.id)}
        emptyText="No orders."
        testId="platform-workspace-orders"
      />
      <OrderSheet tenantId={tenantId} id={open} onClose={() => setOpen(null)} />
    </>
  );
}

interface OrderDetail extends OrderRow {
  recipientName: string | null;
  recipientPhone: string | null;
  addressLine1: string | null;
  addressLine2: string | null;
  city: string | null;
  province: string | null;
  postalCode: string | null;
  countryCode: string | null;
  shippingAmount: string | null;
  paidAt: string | null;
  deliveredAt: string | null;
  items: {
    id: string;
    title: string | null;
    skuAttributes: string | null;
    quantity: number;
    unitPrice: string | null;
    currency: string | null;
  }[];
  shipments: {
    id: string;
    status: string;
    carrier: string | null;
    trackingNumber: string | null;
  }[];
  events: {
    id: string;
    eventType: string;
    description: string | null;
    occurredAt: string;
  }[];
  supplierOrders: {
    id: string;
    status: string;
    externalOrderIds: string[];
    errorMessage: string | null;
    trackingNumber: string | null;
  }[];
}

function OrderSheet({
  tenantId,
  id,
  onClose,
}: {
  tenantId: string;
  id: string | null;
  onClose: () => void;
}) {
  const detail = usePlatformWorkspaceRecord<OrderDetail>(
    tenantId,
    "orders",
    id,
  );
  const d = detail.data;
  return (
    <Sheet open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Order {d?.externalId ?? ""}</SheetTitle>
          <SheetDescription>
            Includes buyer details. Opening it is audited.
          </SheetDescription>
        </SheetHeader>
        {detail.isError ? (
          <ErrorState
            title="Could not load the order"
            onRetry={() => void detail.refetch()}
          />
        ) : !d ? (
          <Skeleton className="mt-4 h-64 w-full" />
        ) : (
          <div
            className="mt-4 space-y-4 text-sm"
            data-testid="platform-order-detail"
          >
            <Facts
              rows={[
                ["Fulfilment", d.fulfillmentStatus],
                ["Payment", d.paymentStatus],
                ["Total", money(d.totalAmount, d.currency)],
                ["Shipping", money(d.shippingAmount, d.currency)],
                ["Paid", when(d.paidAt)],
                ["Delivered", when(d.deliveredAt)],
                ["Recipient", d.recipientName ?? "—"],
                ["Phone", d.recipientPhone ?? "—"],
                [
                  "Address",
                  [
                    d.addressLine1,
                    d.addressLine2,
                    d.city,
                    d.province,
                    d.postalCode,
                    d.countryCode,
                  ]
                    .filter(Boolean)
                    .join(", ") || "—",
                ],
                ["Sync error", d.lastSyncError ?? "—"],
              ]}
            />
            <div>
              <p className="mb-1 font-medium">Items</p>
              <ul className="divide-y rounded border">
                {d.items.map((i) => (
                  <li key={i.id} className="flex justify-between gap-2 p-2">
                    <span>
                      {i.quantity} × {i.title ?? "item"}
                      {i.skuAttributes && (
                        <span className="text-muted-foreground">
                          {" "}
                          ({i.skuAttributes})
                        </span>
                      )}
                    </span>
                    <span className="text-muted-foreground">
                      {money(i.unitPrice, i.currency)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1 font-medium">Supplier order</p>
              {d.supplierOrders.length === 0 ? (
                <p className="text-muted-foreground">None.</p>
              ) : (
                d.supplierOrders.map((s) => (
                  <Facts
                    key={s.id}
                    rows={[
                      ["Status", s.status],
                      [
                        "AliExpress orders",
                        s.externalOrderIds.join(", ") || "—",
                      ],
                      ["Tracking", s.trackingNumber ?? "—"],
                      ["Error", s.errorMessage ?? "—"],
                    ]}
                  />
                ))
              )}
            </div>
            <div>
              <p className="mb-1 font-medium">Shipments</p>
              {d.shipments.length === 0 ? (
                <p className="text-muted-foreground">None.</p>
              ) : (
                <ul className="space-y-1">
                  {d.shipments.map((s) => (
                    <li key={s.id}>
                      {s.status} · {s.carrier ?? "carrier unknown"} ·{" "}
                      {s.trackingNumber ?? "no tracking"}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <p className="mb-1 font-medium">History</p>
              <ul className="space-y-1 text-muted-foreground">
                {d.events.map((e) => (
                  <li key={e.id}>
                    {when(e.occurredAt)} · {e.eventType}
                    {e.description ? ` — ${e.description}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function SyncRunsTab({ tenantId }: { tenantId: string }) {
  const [kind, setKind] = useState<"orders" | "inventory">("orders");
  const columns: Column<RunRow>[] = [
    {
      header: "Status",
      cell: (r) => <StatusBadge value={r.status} bad={["failed", "partial"]} />,
    },
    { header: "Trigger", cell: (r) => r.trigger },
    { header: "Started", cell: (r) => when(r.startedAt) },
    { header: "Finished", cell: (r) => when(r.finishedAt) },
    {
      header: "Error",
      cell: (r) => (
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {r.errorCode ? `${r.errorCode}: ${r.errorMessage ?? ""}` : "—"}
        </span>
      ),
    },
  ];
  return (
    <div className="space-y-3">
      <div className="flex gap-2" role="group" aria-label="Sync kind">
        {(["orders", "inventory"] as const).map((k) => (
          <Button
            key={k}
            size="sm"
            variant={kind === k ? "default" : "outline"}
            aria-pressed={kind === k}
            onClick={() => setKind(k)}
          >
            {k === "orders" ? "Order syncs" : "Inventory syncs"}
          </Button>
        ))}
      </div>
      <PlatformDataTable<RunRow>
        key={kind}
        tenantId={tenantId}
        resource="sync-runs"
        fixed={{ kind }}
        columns={columns}
        searchable={false}
        filters={[
          {
            param: "status",
            label: "Status",
            options: opts("running", "succeeded", "failed", "partial"),
          },
        ]}
        emptyText="No sync runs."
        testId="platform-workspace-runs"
      />
    </div>
  );
}

export function NotificationsTab({ tenantId }: { tenantId: string }) {
  const columns: Column<NotificationRow>[] = [
    { header: "When", cell: (n) => when(n.createdAt) },
    { header: "Kind", cell: (n) => n.kind.replaceAll("_", " ") },
    {
      header: "Message",
      cell: (n) => (
        <div>
          <div className="font-medium">{n.title}</div>
          <div className="line-clamp-2 text-xs text-muted-foreground">
            {n.body}
          </div>
        </div>
      ),
    },
    { header: "Read", cell: (n) => (n.isRead ? "yes" : "no") },
    { header: "Email", cell: (n) => n.emailStatus ?? "—" },
  ];
  return (
    <PlatformDataTable<NotificationRow>
      tenantId={tenantId}
      resource="notifications"
      columns={columns}
      filters={[
        {
          param: "kind",
          label: "Kind",
          options: opts(
            "import_completed",
            "sync_failed",
            "inventory_changed",
            "price_changed",
            "order_imported",
            "shipment_updated",
            "webhook_failure",
            "task_failure",
            "automation_completed",
            "automation_failed",
            "info",
          ),
        },
      ]}
      emptyText="No notifications."
    />
  );
}

export function ExportTab({ tenantId }: { tenantId: string }) {
  const guard = useReauthGuard();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function run(dataset: string) {
    setError(null);
    setBusy(dataset);
    guard(() => downloadWorkspaceExport(tenantId, dataset))
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.code === "reauth_cancelled") return;
        setError(e instanceof ApiError ? e.message : "The export failed.");
      })
      .finally(() => setBusy(null));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Export as CSV</CardTitle>
        <CardDescription>
          Up to 5000 rows each, newest first. Needs your password and a new
          code, and every export is recorded in the audit log with its row
          count.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          className="flex flex-wrap gap-2"
          data-testid="platform-workspace-export"
        >
          {EXPORT_DATASETS.map((d) => (
            <Button
              key={d.value}
              variant="outline"
              disabled={busy !== null}
              onClick={() => run(d.value)}
            >
              {busy === d.value ? "Preparing…" : d.label}
            </Button>
          ))}
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}

export function NoAccess() {
  return (
    <EmptyState
      title="Not available"
      description="Your role cannot see this section."
    />
  );
}
