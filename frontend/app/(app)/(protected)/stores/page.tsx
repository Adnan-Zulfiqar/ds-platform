import type { Metadata } from "next";

import { StoreStatisticsCards } from "@/components/stores/store-statistics-cards";
import { StoreTable } from "@/components/stores/store-table";
import { CreateStoreDialog } from "@/components/stores/create-store-dialog";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Connected Stores" };

export default function StoresPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Connected Stores"
        description="Sales channels, connection health, and per-store sync settings."
        actions={<CreateStoreDialog />}
      />
      <StoreStatisticsCards />
      <StoreTable />
    </div>
  );
}
