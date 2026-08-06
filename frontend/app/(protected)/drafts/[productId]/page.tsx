import type { Metadata } from "next";
import { Suspense } from "react";

import { DraftProductEditor } from "@/components/drafts/draft-product-editor";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Edit Draft" };

/**
 * Full-page draft product editor.
 *
 * Suspense wraps the client island because it reads `useSearchParams` for the
 * active tab — Next requires that boundary for the static shell.
 */
export default async function DraftEditorPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const { productId } = await params;
  return (
    <Suspense
      fallback={
        <div className="space-y-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <DraftProductEditor productId={productId} />
    </Suspense>
  );
}
