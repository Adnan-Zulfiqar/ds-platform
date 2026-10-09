"use client";

import {
  ExportTab,
  ListingsTab,
  NotificationsTab,
  OrdersTab,
  ProductsTab,
  StoresTab,
  SyncRunsTab,
  UsersTab,
} from "@/components/platform/platform-workspace-tabs";
import { PlatformWorkspaceDetail } from "@/components/platform/platform-workspaces";

/** The workspace page and its tabs (D-019). Each tab loads only when
 * opened, and each load is one audited view on the server. */
export function PlatformWorkspacePage({ tenantId }: { tenantId: string }) {
  return (
    <PlatformWorkspaceDetail
      tenantId={tenantId}
      tabs={[
        {
          id: "users",
          label: "Users",
          content: <UsersTab tenantId={tenantId} />,
        },
        {
          id: "stores",
          label: "Stores",
          content: <StoresTab tenantId={tenantId} />,
        },
        {
          id: "products",
          label: "Products",
          content: <ProductsTab tenantId={tenantId} />,
        },
        {
          id: "listings",
          label: "Listings",
          content: <ListingsTab tenantId={tenantId} />,
        },
        {
          id: "orders",
          label: "Orders",
          content: <OrdersTab tenantId={tenantId} />,
        },
        {
          id: "runs",
          label: "Sync runs",
          content: <SyncRunsTab tenantId={tenantId} />,
        },
        {
          id: "notifications",
          label: "Notifications",
          content: <NotificationsTab tenantId={tenantId} />,
        },
        {
          id: "export",
          label: "Export",
          content: <ExportTab tenantId={tenantId} />,
        },
      ]}
    />
  );
}
