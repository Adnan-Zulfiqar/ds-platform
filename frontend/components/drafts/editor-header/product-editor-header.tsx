"use client";

import Link from "next/link";
import { ArrowLeft, ExternalLink } from "lucide-react";

import { MobileEditorActionBar } from "@/components/drafts/editor-header/mobile-editor-action-bar";
import { ProductActionsMenu } from "@/components/drafts/editor-header/product-actions-menu";
import { ProductEditorActions } from "@/components/drafts/editor-header/product-editor-actions";
import {
  ProductEditorTabs,
  type EditorTab,
} from "@/components/drafts/editor-header/product-editor-tabs";
import { ProductThumbnail } from "@/components/drafts/editor-header/product-thumbnail";
import { SaveStateIndicator } from "@/components/drafts/editor-header/save-state-indicator";
import { ShopifyStatus } from "@/components/drafts/editor-header/shopify-status";
import { deriveStoreStatusLabel } from "@/components/drafts/editor-header/store-status-label";
import { deriveSupplierSyncKind } from "@/components/drafts/editor-header/supplier-sync-status";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { EditorLifecycle } from "@/lib/editor-lifecycle";
import type { Store } from "@/services/stores";
import type { ProductDetail, SeoScore } from "@/types/api";

interface ProductEditorHeaderProps {
  productId: string;
  product: ProductDetail;
  activeTab: EditorTab;
  onTabChange: (tab: EditorTab) => void;
  /** Everything the header says about state comes from here — see `lib/editor-lifecycle.ts`. */
  lifecycle: EditorLifecycle;
  dirty: boolean;
  saving: boolean;
  /** A listings request is being retried after an unavailable status. */
  listingsRetrying: boolean;
  /** Workspace Shopify stores — used for connect guidance, not publication authority. */
  shopifyStores: Store[];
  storesPending: boolean;
  storesError: boolean;
  seoScore: SeoScore | null | undefined;
  refreshing: boolean;
  optimizing: boolean;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onResolveConflict: () => void;
  onRetryListings: () => void;
  onRefresh: () => void;
  onOptimize: () => void;
  onViewHistory: () => void;
}

/**
 * The sticky editor header: identity, one status row, actions, sections.
 *
 * The status row is three facts that live in different places and are
 * therefore never contradictory: where the product stands on Shopify (the
 * badge), where the merchant's edits are (the save indicator), and which
 * store is involved (the store label). Before UX-L2D-05 the row could read
 * "Draft · Draft saved — not live" under a "Published successfully" panel,
 * because each element ran its own predicate; now all of them read the
 * lifecycle object the editor derives once.
 */
export function ProductEditorHeader({
  productId,
  product,
  activeTab,
  onTabChange,
  lifecycle,
  dirty,
  saving,
  listingsRetrying,
  shopifyStores,
  storesPending,
  storesError,
  seoScore,
  refreshing,
  optimizing,
  inspectorOpen,
  onToggleInspector,
  onPreview,
  onSave,
  onPublish,
  onResolveConflict,
  onRetryListings,
  onRefresh,
  onOptimize,
  onViewHistory,
}: ProductEditorHeaderProps) {
  const featuredImage = product.images[0]?.url ?? null;
  const { shopify, save, next } = lifecycle;
  const supplierKind = deriveSupplierSyncKind({
    lastSyncedAt: product.lastSyncedAt,
    lastSyncError: product.lastSyncError,
    refreshing,
  });
  // While this session's publish response stands in for the listings cache
  // there is no listing row yet, but the shop is known from the response.
  const storeLabel =
    !shopify.listing && shopify.hasSyncedListing
      ? (shopify.shopDomain ?? "Connected store")
      : deriveStoreStatusLabel({
          storesPending,
          storesError,
          shopifyStores,
          listing: shopify.listing,
        });

  const openSupplier = () => {
    if (product.externalUrl) {
      window.open(product.externalUrl, "_blank", "noopener,noreferrer");
    }
  };

  const supplierLine = product.supplierName
    ? `Imported from ${product.supplierName}`
    : "Imported from AliExpress";

  const menuProps = {
    refreshing,
    optimizing,
    hasExternalUrl: Boolean(product.externalUrl),
    onRefresh,
    onOptimize,
    onOpenAliExpress: openSupplier,
    onViewHistory,
  };

  // No manual Save while a conflict is open: `handleSave` refuses it anyway
  // (M2A), and a button that silently does nothing reads as broken. The
  // banner holds the only two actions that can move things forward.
  const saveDisabled = (!dirty && save.kind !== "save-error") || save.kind === "conflict";
  const actionsProps = {
    productId,
    saving,
    saveDisabled,
    action: next,
    adminUrl: shopify.adminUrl,
    ...menuProps,
    onPreview,
    onSave,
    onPublish,
    onResolveConflict,
  };

  const tabIndicators = {
    media: product.images.length === 0 ? { issues: 1 } : undefined,
    variants: product.variants.length === 0 ? { issues: 1 } : undefined,
    seo:
      typeof seoScore?.score === "number"
        ? { score: seoScore.score }
        : undefined,
    // Presentation advice must not mark Review & publish as blocked — channel
    // checks remain the publish authority once the merchant reaches that tab.
  };

  const title = product.title || "Untitled draft";
  // A product on Shopify is listed under Products; its editor should lead
  // back there rather than to Drafts, where it no longer appears.
  const backHref = shopify.hasSyncedListing ? "/products" : "/drafts";
  const backLabel = shopify.hasSyncedListing ? "Back to products" : "Back to drafts";

  return (
    <>
      <header
        className="sticky top-0 z-20 -mx-1 border-b border-border/80 bg-background/95 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/85"
        data-testid="product-editor-header"
      >
        <div className="space-y-2 px-1 py-2 md:px-2">
          <div className="flex items-start gap-2 md:gap-3">
            <Button
              variant="ghost"
              size="sm"
              className="mt-1 hidden h-11 shrink-0 gap-1.5 px-2 text-muted-foreground md:inline-flex"
              asChild
            >
              <Link href={backHref}>
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                {backLabel}
              </Link>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="mt-1 h-11 w-11 shrink-0 md:hidden"
              asChild
            >
              <Link href={backHref} aria-label={backLabel}>
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              </Link>
            </Button>

            <ProductThumbnail
              url={featuredImage}
              title={title}
              onOpenMedia={() => onTabChange("media")}
            />

            <div className="min-w-0 flex-1">
              <TooltipProvider delayDuration={300}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <h1
                      className="line-clamp-2 text-base font-semibold tracking-tight text-foreground md:text-lg"
                      data-testid="product-editor-title"
                      title={title}
                    >
                      {title}
                    </h1>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" align="start" className="max-w-md">
                    {title}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <span className="sr-only">{title}</span>
              <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-muted-foreground">
                <span className="truncate">{supplierLine}</span>
                {product.externalUrl ? (
                  <a
                    href={product.externalUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-11 items-center gap-1 rounded-sm text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    View supplier product
                  </a>
                ) : null}
              </div>
              <div
                className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"
                data-testid="product-status-group"
              >
                <ShopifyStatus
                  state={shopify}
                  onRetry={onRetryListings}
                  retryInFlight={listingsRetrying}
                />
                <SaveStateIndicator save={save} onRetry={onSave} />
                <span
                  className="truncate text-xs text-muted-foreground"
                  data-testid="product-editor-store"
                >
                  {storeLabel}
                </span>
                {supplierKind === "stale" || supplierKind === "failed" || supplierKind === "never" ? (
                  <span className="text-xs text-amber-700 dark:text-amber-400">
                    Supplier information may be out of date
                  </span>
                ) : null}
                <button
                  type="button"
                  className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-2 text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
                  onClick={onToggleInspector}
                  data-testid="things-to-fix-trigger"
                  aria-expanded={inspectorOpen}
                  aria-controls="publish-checklist-sheet"
                >
                  {inspectorOpen ? "Hide checklist" : "Things to fix"}
                </button>
              </div>
            </div>

            {/* Inline from `xl`: at 1024–1279 with the sidebar open the
                identity column is ~500px and an inline action row squeezed
                the title to one word per line. */}
            <ProductEditorActions
              {...actionsProps}
              className="hidden shrink-0 xl:flex"
            />
            <div className="md:hidden">
              <ProductActionsMenu {...menuProps} />
            </div>
          </div>

          <ProductEditorActions
            {...actionsProps}
            className="hidden md:flex xl:hidden"
          />

          <ProductEditorTabs
            activeTab={activeTab}
            onChange={onTabChange}
            indicators={tabIndicators}
          />
        </div>
      </header>

      <MobileEditorActionBar
        productId={productId}
        saving={saving}
        saveDisabled={saveDisabled}
        action={next}
        onPreview={onPreview}
        onSave={onSave}
        onPublish={onPublish}
        onResolveConflict={onResolveConflict}
      />
    </>
  );
}

export function ProductEditorHeaderSkeleton() {
  return (
    <div className="space-y-3 border-b py-3" data-testid="draft-editor-loading">
      <div className="flex gap-3">
        <Skeleton className="h-14 w-14 rounded-[10px]" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
