import type { Metadata } from "next";

import { ImportProductDialog } from "@/components/products/import-product-dialog";
import { ProductTable } from "@/components/products/product-table";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Drafts" };

/**
 * Draft product inbox (Product Workspace V2).
 *
 * Imports land here by default. Published listings never appear — those live
 * under Products once a channel listing is synced.
 */
export default function DraftsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Drafts"
        description="Imported products awaiting review and Publish to Store."
        actions={<ImportProductDialog />}
      />
      <ProductTable variant="drafts" />
    </div>
  );
}
