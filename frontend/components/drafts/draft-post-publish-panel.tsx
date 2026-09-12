"use client";

import Link from "next/link";
import { CheckCircle2, ChevronDown, ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  deriveProductLifecycle,
  type ProductLifecycleView,
} from "@/lib/product-lifecycle";
import { externalLinkRel, isTrustedShopifyHttpsUrl } from "@/lib/external-link";
import type { ShopifyPublishResult, StoreListing } from "@/types/api";

interface DraftPostPublishPanelProps {
  productId: string;
  storeName?: string | null;
  listing?: StoreListing | null;
  publishResult?: ShopifyPublishResult | null;
  onContinueEditing?: () => void;
  onReviewChanges?: () => void;
}

function headlineFor(lifecycle: ProductLifecycleView): string {
  switch (lifecycle.kind) {
    case "visible_on_shop":
      return "Your product is visible on your shop";
    case "visibility_setup_needed":
      return "Your product was added to Shopify";
    case "added_to_shopify":
      return "Your product was added to Shopify";
    default:
      return "Your product was added to Shopify";
  }
}

export function DraftPostPublishPanel({
  productId,
  storeName,
  listing,
  publishResult,
  onContinueEditing,
  onReviewChanges,
}: DraftPostPublishPanelProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const lifecycle = deriveProductLifecycle({
    syncedListing: listing ?? null,
    publishResult,
  });

  if (!listing && !publishResult) return null;

  const trustedStorefront = isTrustedShopifyHttpsUrl(lifecycle.storefrontUrl)
    ? lifecycle.storefrontUrl
    : null;
  const trustedAdmin = isTrustedShopifyHttpsUrl(lifecycle.adminUrl)
    ? lifecycle.adminUrl
    : null;

  const primaryHref = `/products/${productId}`;
  const showViewInShop = lifecycle.kind === "visible_on_shop" && trustedStorefront;
  const primaryIsExternal = showViewInShop;

  const externalId =
    publishResult?.externalProductId ?? listing?.externalProductId ?? null;
  const handle =
    publishResult?.externalHandle ?? listing?.externalHandle ?? null;
  const shopDomain =
    publishResult?.shopDomain ?? listing?.shopDomain ?? null;

  return (
    <div
      className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4"
      data-testid="post-publish-success"
    >
      <div className="flex items-start gap-3">
        <CheckCircle2
          className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1 space-y-3">
          <div>
            <h3 className="font-semibold text-foreground">{headlineFor(lifecycle)}</h3>
            {storeName ? (
              <p className="mt-1 text-sm text-muted-foreground">Store: {storeName}</p>
            ) : null}
            <p className="mt-1 text-sm text-muted-foreground">{lifecycle.supportingCopy}</p>
            {lifecycle.kind === "visibility_setup_needed" ? (
              <p className="mt-2 text-sm text-amber-800 dark:text-amber-300">
                Finish setup in Shopify to show this product on your online shop. You
                may need to reconnect Shopify if your store permissions changed.
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap gap-2">
            {primaryIsExternal && trustedStorefront ? (
              <Button asChild size="sm" className="min-h-11">
                <a
                  href={trustedStorefront}
                  target="_blank"
                  rel={externalLinkRel()}
                  aria-label="View in your shop (opens in a new tab)"
                >
                  <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
                  View in your shop
                </a>
              </Button>
            ) : (
              <Button asChild size="sm" className="min-h-11">
                <Link href={primaryHref}>View in Products</Link>
              </Button>
            )}

            {trustedAdmin ? (
              <Button asChild size="sm" variant="outline" className="min-h-11">
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

            {onReviewChanges ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="min-h-11"
                onClick={onReviewChanges}
              >
                Continue editing
              </Button>
            ) : onContinueEditing ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="min-h-11"
                onClick={onContinueEditing}
              >
                Continue editing
              </Button>
            ) : null}
          </div>

          {(externalId || handle || shopDomain) && (
            <div>
              <button
                type="button"
                className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground"
                onClick={() => setDetailsOpen((open) => !open)}
                aria-expanded={detailsOpen}
                data-testid="post-publish-details-toggle"
              >
                <ChevronDown
                  className={detailsOpen ? "h-4 w-4 rotate-180 transition-transform" : "h-4 w-4 transition-transform"}
                  aria-hidden="true"
                />
                Details
              </button>
              {detailsOpen ? (
                <dl
                  className="mt-2 space-y-1 rounded-md border border-border/80 bg-background/80 p-3 text-xs text-muted-foreground"
                  data-testid="post-publish-details"
                >
                  {shopDomain ? (
                    <>
                      <dt className="font-medium text-foreground">Shop</dt>
                      <dd>{shopDomain}</dd>
                    </>
                  ) : null}
                  {externalId ? (
                    <>
                      <dt className="mt-2 font-medium text-foreground">Shopify product ID</dt>
                      <dd>{externalId}</dd>
                    </>
                  ) : null}
                  {handle ? (
                    <>
                      <dt className="mt-2 font-medium text-foreground">URL handle</dt>
                      <dd>{handle}</dd>
                    </>
                  ) : null}
                </dl>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
