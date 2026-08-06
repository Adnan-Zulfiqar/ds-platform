"use client";

import Link from "next/link";
import { CheckCircle2, Copy, ExternalLink, Store } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ShopifyPublishResult, StoreListing } from "@/types/api";

interface DraftPostPublishPanelProps {
  listing?: StoreListing | null;
  publishResult?: ShopifyPublishResult | null;
  onContinueEditing?: () => void;
}

export function DraftPostPublishPanel({
  listing,
  publishResult,
  onContinueEditing,
}: DraftPostPublishPanelProps) {
  const storefrontUrl =
    publishResult?.storefrontUrl ?? listing?.storefrontUrl ?? null;
  const adminUrl = publishResult?.adminUrl ?? listing?.adminUrl ?? null;
  const online =
    publishResult?.onlineStorePublished ?? listing?.onlineStorePublished;
  const hasListing = Boolean(listing || publishResult);

  if (!hasListing) return null;

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // clipboard may be unavailable in some browsers
    }
  }

  return (
    <div
      className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4"
      data-testid="post-publish-success"
    >
      <div className="flex items-start gap-3">
        <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" />
        <div className="flex-1 space-y-3">
          <div>
            <h3 className="font-semibold">Product published successfully</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              External ID{" "}
              {publishResult?.externalProductId ?? listing?.externalProductId}
              {publishResult?.externalHandle || listing?.externalHandle
                ? ` · handle ${publishResult?.externalHandle ?? listing?.externalHandle}`
                : null}
            </p>
          </div>

          {online === false ? (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              Created in Shopify, but not visible on the Online Store. Use
              Manage in Shopify to publish to the Online Store channel. Full
              channel publication may require{" "}
              <code className="text-xs">write_publications</code> after
              reauthorization.
            </p>
          ) : null}

          {online == null && !storefrontUrl ? (
            <p className="text-sm text-muted-foreground">
              Online Store visibility is unverified. Manage in Shopify is
              available; View in Store appears only when a storefront URL is
              confirmed.
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {storefrontUrl && online !== false ? (
              <Button asChild size="sm">
                <a href={storefrontUrl} target="_blank" rel="noreferrer">
                  <ExternalLink className="mr-2 h-4 w-4" />
                  View in Store
                </a>
              </Button>
            ) : null}
            {adminUrl ? (
              <Button asChild size="sm" variant="outline">
                <a href={adminUrl} target="_blank" rel="noreferrer">
                  <Store className="mr-2 h-4 w-4" />
                  Manage in Shopify
                </a>
              </Button>
            ) : null}
            {storefrontUrl ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void copyUrl(storefrontUrl)}
              >
                <Copy className="mr-2 h-4 w-4" />
                Copy Product URL
              </Button>
            ) : null}
            <Button asChild size="sm" variant="outline">
              <Link href="/products">View in DropPilot Products</Link>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onContinueEditing}
            >
              Continue Editing
            </Button>
            <Button asChild size="sm" variant="ghost">
              <Link href="/drafts">Publish Another Product</Link>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
