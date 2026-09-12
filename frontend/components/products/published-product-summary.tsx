"use client";

import Link from "next/link";
import { ArrowLeft, ExternalLink } from "lucide-react";

import { ProductThumbnail } from "@/components/drafts/editor-header/product-thumbnail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deriveProductLifecycle,
  type ProductLifecycleView,
} from "@/lib/product-lifecycle";
import { externalLinkRel, isTrustedShopifyHttpsUrl } from "@/lib/external-link";
import { formatMoney } from "@/lib/utils";
import { ApiError } from "@/lib/api-client";
import { useDraftListings } from "@/services/drafts";
import { useProduct } from "@/services/products";

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function shortTitle(title: string, max = 48): string {
  if (title.length <= max) return title;
  return `${title.slice(0, max - 1)}…`;
}

function primaryAction(
  productId: string,
  lifecycle: ProductLifecycleView,
): { label: string; href: string; external?: boolean } {
  const storefront = isTrustedShopifyHttpsUrl(lifecycle.storefrontUrl)
    ? lifecycle.storefrontUrl
    : null;
  if (lifecycle.kind === "visible_on_shop" && storefront) {
    return { label: "View in your shop", href: storefront, external: true };
  }
  return { label: "Edit in DropPilot", href: `/drafts/${productId}?tab=overview` };
}

interface PublishedProductSummaryProps {
  productId: string;
}

export function PublishedProductSummary({ productId }: PublishedProductSummaryProps) {
  const productQuery = useProduct(productId);
  const listingsQuery = useDraftListings(productId);

  if (productQuery.isPending || listingsQuery.isPending) {
    return (
      <div className="space-y-4" data-testid="published-product-loading">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (productQuery.isError) {
    const isNotFound =
      productQuery.error instanceof ApiError && productQuery.error.status === 404;
    if (isNotFound) {
      return (
        <div data-testid="published-product-not-found">
          <ErrorState
            title="Product not found"
            description="This product may belong to another workspace or no longer exists."
          />
          <div className="mt-4 flex justify-center">
            <Button asChild variant="outline">
              <Link href="/products">Back to Products</Link>
            </Button>
          </div>
        </div>
      );
    }
    return (
      <div data-testid="published-product-error">
        <ErrorState
          title="Could not load product"
          description={
            productQuery.error instanceof Error
              ? productQuery.error.message
              : "Please try again."
          }
          onRetry={() => void productQuery.refetch()}
        />
      </div>
    );
  }

  const product = productQuery.data;
  const syncedListing =
    listingsQuery.data?.find((row) => row.status === "synced") ??
    listingsQuery.data?.[0] ??
    null;

  const lifecycle = deriveProductLifecycle({
    syncedListing,
    listingsError: listingsQuery.isError,
    listingsLoading: listingsQuery.isPending,
  });

  const featuredImage = product.images[0]?.url ?? null;
  const displayTitle = product.title || "Untitled product";
  const sellPrice =
    product.sellPrice ??
    product.variants.find((v) => v.sellPrice)?.sellPrice ??
    null;
  const stock =
    product.stockQuantity > 0 ? product.stockQuantity : product.variants[0]?.stockQuantity;
  const plainDescription = product.description ? stripHtml(product.description) : null;
  const action = primaryAction(productId, lifecycle);
  const trustedAdmin = isTrustedShopifyHttpsUrl(syncedListing?.adminUrl ?? null)
    ? syncedListing?.adminUrl
    : null;

  return (
    <div className="space-y-6" data-testid="published-product-summary">
      <nav aria-label="Breadcrumb" className="text-sm text-muted-foreground">
        <Link href="/products" className="hover:text-foreground hover:underline">
          Products
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="text-foreground">{shortTitle(displayTitle)}</span>
      </nav>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <ProductThumbnail
          url={featuredImage}
          title={displayTitle}
          className="h-20 w-20 shrink-0"
        />
        {!featuredImage ? (
          <span className="sr-only">Product image unavailable</span>
        ) : null}

        <div className="min-w-0 flex-1 space-y-3">
          <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{displayTitle}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant={
                lifecycle.kind === "visible_on_shop" ? "default" : "secondary"
              }
              data-testid="published-product-lifecycle"
            >
              {lifecycle.badgeLabel}
            </Badge>
            {syncedListing?.shopDomain ? (
              <span className="text-sm text-muted-foreground">{syncedListing.shopDomain}</span>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">{lifecycle.supportingCopy}</p>
        </div>
      </div>

      <section className="grid gap-4 rounded-lg border bg-card p-4 sm:grid-cols-2">
        {sellPrice ? (
          <div>
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Selling price
            </h2>
            <p className="mt-1 text-sm font-medium">
              {formatMoney(sellPrice, product.currency ?? undefined)}
            </p>
          </div>
        ) : null}
        {typeof stock === "number" ? (
          <div>
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Stock
            </h2>
            <p className="mt-1 text-sm font-medium">{stock.toLocaleString()}</p>
          </div>
        ) : null}
        {plainDescription ? (
          <div className="sm:col-span-2">
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Description
            </h2>
            <p className="mt-1 line-clamp-4 text-sm text-muted-foreground">{plainDescription}</p>
          </div>
        ) : null}
        {!sellPrice && typeof stock !== "number" && !plainDescription ? (
          <p className="text-sm text-muted-foreground sm:col-span-2">
            No price or stock details are available yet.
          </p>
        ) : null}
      </section>

      <div className="flex flex-wrap gap-2">
        {action.external ? (
          <Button asChild className="min-h-11">
            <a
              href={action.href}
              target="_blank"
              rel={externalLinkRel()}
              aria-label={`${action.label} (opens in a new tab)`}
            >
              <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
              {action.label}
            </a>
          </Button>
        ) : (
          <Button asChild className="min-h-11">
            <Link href={action.href}>{action.label}</Link>
          </Button>
        )}
        {trustedAdmin ? (
          <Button asChild variant="outline" className="min-h-11">
            <a
              href={trustedAdmin}
              target="_blank"
              rel={externalLinkRel()}
              aria-label="Open Shopify (opens in a new tab)"
            >
              Open Shopify
            </a>
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
