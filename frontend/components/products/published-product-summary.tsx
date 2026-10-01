"use client";

import { ArrowLeft, ExternalLink, Loader2, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useCanUseStudio } from "@/components/ai-studio/studio-access";
import { ProductThumbnail } from "@/components/drafts/editor-header/product-thumbnail";
import { ProductNotFound } from "@/components/products/product-not-found";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { stripHtml } from "@/lib/ai-studio/text";
import { ApiError } from "@/lib/api-client";
import { EXTERNAL_LINK_REL, isTrustedShopifyHttpsUrl } from "@/lib/external-link";
import { deriveListingLifecycle, draftNewerThanSync } from "@/lib/listing-lifecycle";
import { formatDateTime, formatMoney } from "@/lib/utils";
import { useDraftListings } from "@/services/drafts";
import { useProduct } from "@/services/products";

/**
 * The published product page (UX-L2D-04), adapted from the historical
 * UX-L2C `PublishedProductSummary` under UX-L2D-GATE-04.
 *
 * What was kept: two real endpoints (`GET /products/{id}` and
 * `GET /drafts/{id}/listings`), the non-disclosing not-found, the plain-text
 * description, the HTTPS/`*.myshopify.com` allowlist for outbound links, the
 * listing-status retry with a live region, and the `Edit in DropPilot` path
 * back to the editor.
 *
 * What was changed: a product with **no synced listing is not shown here.**
 * The historical page rendered a same-tenant draft id as though it were a
 * published product; this page sends the merchant to the draft editor
 * instead, because the Products route exists for products the backend
 * itself classifies as published (`_synced_listing_exists`). The decision
 * waits for a confirmed listings response — an unavailable status is shown
 * as unavailable, never treated as "not published".
 */

function shortTitle(title: string, max = 48): string {
  return title.length <= max ? title : `${title.slice(0, max - 1)}…`;
}

interface PublishedProductSummaryProps {
  productId: string;
}

export function PublishedProductSummary({ productId }: PublishedProductSummaryProps) {
  const router = useRouter();
  const productQuery = useProduct(productId);
  const listingsQuery = useDraftListings(productId);
  const { allowed: canUseStudio } = useCanUseStudio();
  const lifecycle = deriveListingLifecycle(listingsQuery);

  // Confirmed not published → this is a draft; take the merchant to where it
  // can be worked on. `replace`, so Back does not bounce through this page.
  const isDraft = productQuery.isSuccess && lifecycle.kind === "not-published";
  useEffect(() => {
    if (isDraft) router.replace(`/drafts/${productId}`);
  }, [isDraft, productId, router]);

  if (productQuery.isPending) {
    return (
      <div className="space-y-4" data-testid="published-product-loading" aria-busy="true" aria-label="Loading product">
        <Skeleton className="h-5 w-40" />
        <div className="flex gap-4">
          <Skeleton className="h-20 w-20 shrink-0 rounded-[10px]" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-7 w-2/3" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        </div>
        <Skeleton className="h-32 w-full rounded-lg" />
      </div>
    );
  }

  if (productQuery.isError) {
    if (productQuery.error instanceof ApiError && productQuery.error.status === 404) {
      return <ProductNotFound />;
    }
    return (
      <div data-testid="published-product-error">
        <ErrorState
          title="Could not load product"
          description="Please try again."
          requestId={productQuery.error instanceof ApiError ? productQuery.error.requestId : null}
          onRetry={() => void productQuery.refetch()}
        />
      </div>
    );
  }

  const product = productQuery.data;
  const displayTitle = product.title || "Untitled product";

  if (isDraft) {
    return (
      <div className="space-y-4" data-testid="published-product-draft-redirect">
        <p className="text-sm text-muted-foreground" role="status">
          <span className="font-medium text-foreground">{displayTitle}</span> has not been
          published to a store yet. Opening it in the draft editor…
        </p>
        <Button asChild>
          <Link href={`/drafts/${productId}`}>Open draft</Link>
        </Button>
      </div>
    );
  }

  const listing = lifecycle.listing;
  const storefrontUrl = isTrustedShopifyHttpsUrl(listing?.storefrontUrl) ? listing?.storefrontUrl : null;
  const adminUrl = isTrustedShopifyHttpsUrl(listing?.adminUrl) ? listing?.adminUrl : null;
  const featuredImage = product.images[0]?.url ?? null;
  const sellPrice =
    product.sellPrice ?? product.variants.find((v) => v.sellPrice)?.sellPrice ?? null;
  const stock =
    product.stockQuantity > 0 ? product.stockQuantity : (product.variants[0]?.stockQuantity ?? null);
  const plainDescription = product.description ? stripHtml(product.description) : "";
  const unsentChanges = listing ? draftNewerThanSync(product.updatedAt, listing.lastSyncedAt) : null;
  const statusUnresolved = lifecycle.kind === "checking" || lifecycle.kind === "unavailable";
  const liveMessage = [lifecycle.label, lifecycle.detail].join(". ");

  return (
    <div className="space-y-6" data-testid="published-product-summary">
      <nav aria-label="Product breadcrumb" className="text-sm text-muted-foreground">
        <Link href="/products" className="underline-offset-4 hover:text-foreground hover:underline">
          Products
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="text-foreground">{shortTitle(displayTitle)}</span>
      </nav>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <ProductThumbnail url={featuredImage} title={displayTitle} className="h-20 w-20 md:h-20 md:w-20" />
        <div className="min-w-0 flex-1 space-y-3">
          <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{displayTitle}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant={
                lifecycle.kind === "visible-on-shop"
                  ? "success"
                  : lifecycle.kind === "added-to-shopify"
                    ? "default"
                    : "secondary"
              }
              data-testid="published-product-lifecycle"
              data-kind={lifecycle.kind}
            >
              {lifecycle.label}
            </Badge>
            {listing?.shopDomain && (
              <span className="text-sm text-muted-foreground">{listing.shopDomain}</span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">{lifecycle.detail}</p>
          <div aria-live="polite" aria-atomic="true" className="sr-only">
            {liveMessage}
          </div>

          {lifecycle.kind === "unavailable" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={listingsQuery.isFetching}
              onClick={() => void listingsQuery.refetch()}
              data-testid="shopify-status-retry"
            >
              {listingsQuery.isFetching ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  Checking…
                </>
              ) : (
                <>
                  <RotateCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
                  Try again
                </>
              )}
            </Button>
          )}
          {lifecycle.refreshFailed && (
            <p className="text-xs text-muted-foreground" data-testid="shopify-status-note">
              Couldn’t refresh Shopify status; showing the last known state.
            </p>
          )}
          {listing && unsentChanges === true && (
            <p className="text-xs text-muted-foreground" data-testid="unsent-changes-note">
              Your DropPilot draft was saved after the last Shopify sync; those changes may not be
              on Shopify yet.
            </p>
          )}
        </div>
      </div>

      <section className="grid gap-4 rounded-lg border bg-card p-4 sm:grid-cols-2" aria-label="Product details">
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Selling price</h2>
          <p className="mt-1 text-sm font-medium">
            {sellPrice ? formatMoney(sellPrice, product.currency) : "Not set"}
          </p>
        </div>
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Stock</h2>
          <p className="mt-1 text-sm font-medium tabular-nums">
            {typeof stock === "number" ? stock.toLocaleString() : "Unknown"}
          </p>
        </div>
        {listing && (
          <div>
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Last synced</h2>
            <p className="mt-1 text-sm font-medium">{formatDateTime(listing.lastSyncedAt)}</p>
          </div>
        )}
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Source</h2>
          <p className="mt-1 text-sm font-medium">
            {product.source === "aliexpress" ? "AliExpress" : "Manual"}
            {product.supplierName && product.supplierName.toLowerCase() !== product.source
              ? ` · ${product.supplierName}`
              : ""}
          </p>
        </div>
        <div className="sm:col-span-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Description</h2>
          {plainDescription ? (
            <p className="mt-1 line-clamp-4 text-sm text-muted-foreground" data-testid="published-product-description">
              {plainDescription}
            </p>
          ) : (
            <p className="mt-1 text-sm text-muted-foreground">No description yet.</p>
          )}
        </div>
      </section>

      <div className="flex flex-wrap gap-2">
        {lifecycle.kind === "visible-on-shop" && storefrontUrl ? (
          <Button asChild className="min-h-11">
            <a
              href={storefrontUrl}
              target="_blank"
              rel={EXTERNAL_LINK_REL}
              aria-label="View in your shop (opens in a new tab)"
              data-testid="storefront-link"
            >
              <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
              View in your shop
            </a>
          </Button>
        ) : (
          <Button asChild className="min-h-11" variant={statusUnresolved ? "outline" : "default"}>
            <Link href={`/drafts/${productId}?tab=overview`}>Edit in DropPilot</Link>
          </Button>
        )}
        {adminUrl && (
          <Button asChild variant="outline" className="min-h-11">
            <a
              href={adminUrl}
              target="_blank"
              rel={EXTERNAL_LINK_REL}
              aria-label="Manage in Shopify (opens in a new tab)"
              data-testid="admin-link"
            >
              Manage in Shopify
            </a>
          </Button>
        )}
        {canUseStudio ? (
          <Button asChild variant="outline" className="min-h-11">
            <Link href={`/ai-studio/products/${productId}`} data-testid="published-ai-studio-link">
              Optimize in AI Studio
            </Link>
          </Button>
        ) : null}
        <Button asChild variant="ghost" className="min-h-11">
          <Link href="/products">
            <ArrowLeft className="mr-2 h-4 w-4" aria-hidden="true" />
            Back to Products
          </Link>
        </Button>
      </div>
    </div>
  );
}
