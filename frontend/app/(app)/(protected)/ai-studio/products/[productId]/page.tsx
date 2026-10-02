import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductReview } from "@/components/ai-studio/product-review";
import { StudioAccess } from "@/components/ai-studio/studio-access";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "AI Studio" };

// Same gate as /products/[productId]: an id that could never be a UUID gets
// the not-found answer without a request (a FastAPI 422 would read
// differently from the 404 a missing or foreign id gets).
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * `/ai-studio/products/[productId]` — one product's AI review (Phase 9
 * Stage 10). Valid for drafts and published products alike; it never follows
 * the draft redirect that `/products/[id]` applies.
 */
export default async function AiStudioProductPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const { productId } = await params;
  if (!UUID_PATTERN.test(productId)) {
    return (
      <div data-testid="ai-studio-product-not-found">
        <ErrorState
          title="Product not found"
          description="This product may have been removed, or the link may be incorrect."
        />
      </div>
    );
  }

  return (
    <StudioAccess>
      {/* `useSearchParams` (the pinned ?candidate=) needs a Suspense boundary. */}
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <ProductReview productId={productId} />
      </Suspense>
    </StudioAccess>
  );
}
