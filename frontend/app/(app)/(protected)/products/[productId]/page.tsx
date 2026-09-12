import { Suspense } from "react";

import { PublishedProductSummary } from "@/components/products/published-product-summary";
import { Skeleton } from "@/components/ui/skeleton";

interface ProductDetailPageProps {
  params: Promise<{ productId: string }>;
}

export default async function ProductDetailPage({ params }: ProductDetailPageProps) {
  const { productId } = await params;

  return (
    <Suspense
      fallback={
        <div className="space-y-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      }
    >
      <PublishedProductSummary productId={productId} />
    </Suspense>
  );
}
