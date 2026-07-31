import type { Metadata } from "next";

import { InventoryTable } from "@/components/inventory/inventory-table";
import { SyncInventoryButton } from "@/components/inventory/sync-inventory-button";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Inventory" };

export default function InventoryPage() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Inventory"
        description="Stock levels synchronised from AliExpress, with last-sync timestamps."
        actions={<SyncInventoryButton />}
      />
      <InventoryTable />
    </div>
  );
}
