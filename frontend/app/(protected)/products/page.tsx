import type { Metadata } from "next";

import { ProductTable } from "@/components/products/product-table";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Products" };

/**
 * Published catalogue (Product Workspace V2).
 *
 * Only products with at least one synced channel listing. Supplier ingestion
 * uses Import as Draft on the Drafts page — not this list.
 */
export default function ProductsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Products"
        description="Products successfully published to at least one connected store."
      />
      <ProductTable variant="products" />
    </div>
  );
}
