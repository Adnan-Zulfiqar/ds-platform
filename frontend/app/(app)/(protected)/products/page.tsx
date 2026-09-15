import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductTable } from "@/components/products/product-table";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";

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
      {/* The table reads its search/sort/page state from the URL, which
          needs a Suspense boundary above any `useSearchParams` consumer. */}
      <Suspense fallback={<Skeleton className="h-64 w-full" />}>
        <ProductTable variant="products" />
      </Suspense>
    </div>
  );
}
