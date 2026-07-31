import type { Metadata } from "next";

import { ImportProductDialog } from "@/components/products/import-product-dialog";
import { ProductTable } from "@/components/products/product-table";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Products" };

/**
 * The product catalogue.
 *
 * A Server Component rendering two client islands: the table owns its own data
 * fetching and the dialog owns its own form state. Neither needs this page to
 * hold state on its behalf, so it does not become a client component merely to
 * pass props down.
 */
export default function ProductsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Products"
        description="Products imported from your connected suppliers."
        actions={<ImportProductDialog />}
      />
      <ProductTable />
    </div>
  );
}
