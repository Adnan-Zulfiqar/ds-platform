import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductNotFound } from "@/components/products/product-not-found";
import { PublishedProductSummary } from "@/components/products/published-product-summary";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Product" };

/**
 * `/products/[productId]` (UX-L2D-04).
 *
 * Thin route, adapted from the reviewed historical UX-L2C page: it inherits
 * `AuthGuard` and `PageContainer` from the protected layout and only decides
 * whether the id is worth asking the API about.
 *
 * A path segment that is not a UUID is answered here as "Product not found",
 * with no request made. FastAPI would reject it with a 422, which the client
 * used to render as a generic API error — a different, and more revealing,
 * answer than the 404 a missing or foreign id gets. Every bad id now reads
 * the same.
 */

// Hyphenated 8-4-4-4-12 hex, any version: the API accepts any UUID, so the
// gate rejects only what could never be one.
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const { productId } = await params;
  if (!UUID_PATTERN.test(productId)) {
    return <ProductNotFound />;
  }

  return (
    <Suspense
      fallback={
        <div className="space-y-4" aria-busy="true" aria-label="Loading product">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      }
    >
      <PublishedProductSummary productId={productId} />
    </Suspense>
  );
}
