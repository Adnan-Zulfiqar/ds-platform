import type { Metadata } from "next";

import { OrderStatisticsCards } from "@/components/orders/order-statistics-cards";
import { OrderTable } from "@/components/orders/order-table";
import { EbayImportOrdersButton } from "@/components/orders/ebay-orders";
import { SyncOrdersDialog } from "@/components/orders/sync-orders-dialog";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Orders" };

/**
 * The order list.
 *
 * A Server Component rendering client islands, the same shape as the products
 * page: statistics, the table (which owns its filters and pagination), and the
 * sync dialog each own their own state and data fetching.
 */
export default function OrdersPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Orders"
        description="Orders synchronised from your connected suppliers and sales channels."
        actions={
          <div className="flex flex-wrap items-start gap-2">
            <EbayImportOrdersButton />
            <SyncOrdersDialog />
          </div>
        }
      />
      <OrderStatisticsCards />
      <OrderTable />
    </div>
  );
}
