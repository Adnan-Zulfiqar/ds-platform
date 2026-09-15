import Link from "next/link";

import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";

/**
 * The one answer for a product that cannot be shown: missing, belonging to
 * another workspace, or requested with an id that is not a UUID at all.
 *
 * Deliberately identical in every case. A different message for a foreign
 * tenant's id would confirm the row exists — the same reason the API answers
 * 404 rather than 403 — and a different one for a malformed id would tell a
 * probe which ids are well-formed. So there is nothing to learn here except
 * the way back.
 */
export function ProductNotFound() {
  return (
    <div data-testid="published-product-not-found">
      <ErrorState
        title="Product not found"
        description="This product may have been removed, or the link may be incorrect."
      />
      <div className="mt-4 flex justify-center">
        <Button asChild variant="outline">
          <Link href="/products">Back to Products</Link>
        </Button>
      </div>
    </div>
  );
}
