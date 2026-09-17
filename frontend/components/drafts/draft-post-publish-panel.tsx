"use client";

import Link from "next/link";
import { CheckCircle2, ChevronDown, Copy, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { ShopifyState } from "@/lib/editor-lifecycle";
import { EXTERNAL_LINK_REL } from "@/lib/external-link";
import type { ShopifyPublishResult } from "@/types/api";

interface DraftPostPublishPanelProps {
  productId: string;
  shopify: ShopifyState;
  /** This session's publish response, when the panel follows a publish. */
  publishResult: ShopifyPublishResult | null;
  storeName?: string | null;
  onContinueEditing: () => void;
}

function headlineFor(shopify: ShopifyState, justPublished: boolean): string {
  switch (shopify.kind) {
    case "publishing":
      return "Sending to Shopify…";
    case "visible-on-shop":
      return justPublished ? "Published — visible on your shop" : "This product is visible on your shop";
    case "changes-not-sent":
      return "On Shopify — your latest changes have not been sent";
    default:
      return justPublished ? "Published to Shopify" : "This product is on Shopify";
  }
}

/**
 * What happened, and what the merchant can do next, once a product is on
 * Shopify.
 *
 * Adapted from the reviewed historical panel (UX-L2D-GATE-04): the
 * lifecycle-driven headline, the trusted-link rule and the collapsed
 * details are kept; the copy-link action from the current panel is kept
 * too. Gone: the raw `write_publications` scope name and the "External ID
 * … handle …" line as the headline's subtitle — identifiers are details,
 * not the message. "Published successfully" is only said after a publish
 * in this session; a product that was already on Shopify when the editor
 * opened is described, not congratulated.
 *
 * Every state here needs listing evidence: the headline says "visible" only
 * on `onlineStorePublished === true`, links render only for HTTPS
 * `*.myshopify.com` URLs the server supplied, and the internal "View
 * product" (UX-L2D-04) is the primary action whenever visibility is not
 * confirmed.
 */
export function DraftPostPublishPanel({
  productId,
  shopify,
  publishResult,
  storeName,
  onContinueEditing,
}: DraftPostPublishPanelProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const justPublished = publishResult !== null;
  const visible = shopify.kind === "visible-on-shop" && Boolean(shopify.storefrontUrl);
  const externalId = shopify.listing?.externalProductId ?? publishResult?.externalProductId ?? null;
  const handle = shopify.listing?.externalHandle ?? publishResult?.externalHandle ?? null;
  const shopDomain = shopify.shopDomain;

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Clipboard access can be refused; the link itself is still on screen.
    }
  }

  return (
    <div
      className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4"
      data-testid="post-publish-success"
      data-kind={shopify.kind}
    >
      <div className="flex items-start gap-3">
        {shopify.kind === "publishing" ? (
          <Loader2
            className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-emerald-600 motion-reduce:animate-none dark:text-emerald-400"
            aria-hidden="true"
          />
        ) : (
          <CheckCircle2
            className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400"
            aria-hidden="true"
          />
        )}
        <div className="min-w-0 flex-1 space-y-3">
          <div>
            <h3 className="font-semibold text-foreground" data-testid="post-publish-headline">
              {headlineFor(shopify, justPublished)}
            </h3>
            {storeName ? (
              <p className="mt-1 text-sm text-muted-foreground">Store: {storeName}</p>
            ) : null}
            <p className="mt-1 text-sm text-muted-foreground" data-testid="post-publish-detail">
              {shopify.detail}
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            {visible && shopify.storefrontUrl ? (
              <Button asChild size="sm" className="min-h-11 md:min-h-9">
                <a href={shopify.storefrontUrl} target="_blank" rel={EXTERNAL_LINK_REL} data-testid="post-publish-storefront">
                  <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
                  View in your shop
                </a>
              </Button>
            ) : (
              <Button asChild size="sm" className="min-h-11 md:min-h-9">
                <Link href={`/products/${productId}`} data-testid="post-publish-view-product">
                  View product
                </Link>
              </Button>
            )}
            {shopify.adminUrl ? (
              <Button asChild size="sm" variant="outline" className="min-h-11 md:min-h-9">
                <a href={shopify.adminUrl} target="_blank" rel={EXTERNAL_LINK_REL} data-testid="post-publish-admin">
                  Manage in Shopify
                </a>
              </Button>
            ) : null}
            {visible && shopify.storefrontUrl ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="min-h-11 md:min-h-9"
                onClick={() => void copyUrl(shopify.storefrontUrl as string)}
              >
                <Copy className="mr-2 h-4 w-4" aria-hidden="true" />
                Copy link
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="min-h-11 md:min-h-9"
              onClick={onContinueEditing}
            >
              Continue editing
            </Button>
            <Button asChild size="sm" variant="ghost" className="min-h-11 md:min-h-9">
              <Link href="/drafts">Publish another product</Link>
            </Button>
          </div>

          {externalId || handle || shopDomain ? (
            <div>
              <button
                type="button"
                className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                  className="mt-2 grid gap-x-6 gap-y-1 rounded-md border border-border/80 bg-background/80 p-3 text-xs text-muted-foreground sm:grid-cols-[auto_1fr]"
                  data-testid="post-publish-details"
                >
                  {shopDomain ? (
                    <>
                      <dt className="font-medium text-foreground">Shop</dt>
                      <dd className="break-all">{shopDomain}</dd>
                    </>
                  ) : null}
                  {externalId ? (
                    <>
                      <dt className="font-medium text-foreground">Shopify product ID</dt>
                      <dd className="break-all">{externalId}</dd>
                    </>
                  ) : null}
                  {handle ? (
                    <>
                      <dt className="font-medium text-foreground">URL handle</dt>
                      <dd className="break-all">{handle}</dd>
                    </>
                  ) : null}
                </dl>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
