import type { Metadata } from "next";
import { Suspense } from "react";

import { ImportProductDialog } from "@/components/products/import-product-dialog";
import { ProductTable } from "@/components/products/product-table";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";

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
      {/* The table reads its search/sort/page state from the URL, which
          needs a Suspense boundary above any `useSearchParams` consumer. */}
      <Suspense fallback={<Skeleton className="h-64 w-full" />}>
        <ProductTable variant="drafts" />
      </Suspense>
    </div>
  );
}
