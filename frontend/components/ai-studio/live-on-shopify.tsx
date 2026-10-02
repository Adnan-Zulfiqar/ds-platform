"use client";

import { useDraftListings } from "@/services/drafts";
import { useProductVersions } from "@/services/products";

/**
 * What the selected store shows now (plan §5a, review finding E-1).
 *
 * Read from the listing's `contentSource` / `contentVersionId` — never from
 * `candidateActive`: an approved version is not necessarily a published one.
 * The listing carries a version id only, so the number comes from this page's
 * candidate when the ids match, then from version history, and otherwise the
 * line says "an approved AI version" rather than guessing.
 */
export function LiveOnShopify({
  productId,
  storeId,
  candidateVersionId,
  candidateVersionNumber,
}: {
  productId: string;
  storeId: string | null;
  candidateVersionId: string | null;
  candidateVersionNumber: number | null;
}) {
  const listings = useDraftListings(productId);
  const versions = useProductVersions(productId);

  if (storeId === null) return null;

  let text: string;
  if (listings.isPending) text = "Checking…";
  else if (listings.isError) text = "Unavailable — the listing could not be loaded.";
  else {
    const listing = listings.data.find((row) => row.storeId === storeId);
    if (!listing) text = "Not published to this store yet";
    else if (listing.contentSource !== "ai_version") text = "Your draft text";
    else {
      const versionId = listing.contentVersionId ?? null;
      const number =
        versionId !== null && versionId === candidateVersionId
          ? candidateVersionNumber
          : versions.data?.items.find((row) => row.id === versionId)?.versionNumber ?? null;
      text = number !== null ? `Approved AI version ${number}` : "An approved AI version";
    }
  }

  return (
    <p className="text-sm" data-testid="ai-studio-live-on-shopify">
      <span className="font-medium">Live on Shopify:</span> {text}
    </p>
  );
}
