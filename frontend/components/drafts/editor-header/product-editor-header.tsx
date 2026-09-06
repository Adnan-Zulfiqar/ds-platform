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
import type { PublishActionKind } from "@/components/drafts/editor-header/publish-action";
import {
  type ReadinessSummary,
} from "@/components/drafts/editor-header/readiness";
import {
  SaveStateIndicator,
  type SaveState,
} from "@/components/drafts/editor-header/save-state-indicator";
import { deriveSupplierSyncKind } from "@/components/drafts/editor-header/supplier-sync-status";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ProductDetail, SeoScore, StoreListing } from "@/types/api";
import type { Store } from "@/services/stores";
import { deriveStoreStatusLabel } from "@/components/drafts/editor-header/store-status-label";

interface ProductEditorHeaderProps {
  product: ProductDetail;
  activeTab: EditorTab;
  onTabChange: (tab: EditorTab) => void;
  dirty: boolean;
  saveState: SaveState;
  saving: boolean;
  publishPending: boolean;
  publishFailed: boolean;
  listing: StoreListing | null;
  /** Workspace Shopify stores — used for connect guidance, not publication authority. */
  shopifyStores: Store[];
  storesPending: boolean;
  storesError: boolean;
  seoScore: SeoScore | null | undefined;
  readiness: ReadinessSummary;
  refreshing: boolean;
  optimizing: boolean;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onRefresh: () => void;
  onOptimize: () => void;
  onViewHistory: () => void;
}

function deriveLifecycle(params: {
  productStatus: string;
  publishPending: boolean;
  publishFailed: boolean;
  listing: StoreListing | null;
}): "Draft" | "Publishing" | "Published" | "Publish failed" | "Archived" {
  if (params.productStatus === "archived") return "Archived";
  if (params.publishPending) return "Publishing";
  if (params.publishFailed || params.listing?.status === "error") {
    return "Publish failed";
  }
  if (params.listing?.status === "synced") return "Published";
  return "Draft";
}

function derivePublishKind(params: {
  publishPending: boolean;
  publishFailed: boolean;
  listing: StoreListing | null;
  dirty: boolean;
  /** Client checklist item count — same gate as pre-L2A (all items). */
  issueCount: number;
}): PublishActionKind {
  if (params.publishPending) return "publishing";
  if (params.publishFailed || params.listing?.status === "error") return "retry";
  if (params.listing?.status === "synced") {
    return params.dirty ? "push_updates" : "view_store";
  }
  // Presentation advice only changes the CTA label to Review N items.
  // It must not invent Required/Recommended or hard-disable publishing —
  // channel checks remain authoritative on the Review & publish tab.
  if (params.issueCount > 0) return "review_items";
  return "publish";
}

export function ProductEditorHeader({
  product,
  activeTab,
  onTabChange,
  dirty,
  saveState,
  saving,
  publishPending,
  publishFailed,
  listing,
  shopifyStores,
  storesPending,
  storesError,
  seoScore,
  readiness,
  refreshing,
  optimizing,
  inspectorOpen,
  onToggleInspector,
  onPreview,
  onSave,
  onPublish,
  onRefresh,
  onOptimize,
  onViewHistory,
}: ProductEditorHeaderProps) {
  const featuredImage = product.images[0]?.url ?? null;
  const lifecycle = deriveLifecycle({
    productStatus: product.status,
    publishPending,
    publishFailed,
    listing,
  });
  const issueCount = readiness.items.length;
  const publishKind = derivePublishKind({
    publishPending,
    publishFailed,
    listing,
    dirty,
    issueCount,
  });
  const supplierKind = deriveSupplierSyncKind({
    lastSyncedAt: product.lastSyncedAt,
    lastSyncError: product.lastSyncError,
    refreshing,
  });
  const storeLabel = deriveStoreStatusLabel({
    storesPending,
    storesError,
    shopifyStores,
    listing,
  });

  // Advisory checklist items never disable the CTA — there is no tooltip reason.
  const disabledPublishReason: string | null = null;

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
    onGoHistoryTab: () => onTabChange("history"),
  };

  const actionsProps = {
    saving,
    saveDisabled: !dirty && saveState !== "error",
    dirty,
    publishKind,
    issueCount,
    disabledPublishReason,
    storefrontUrl: listing?.storefrontUrl,
    adminUrl: listing?.adminUrl,
    ...menuProps,
    onPreview,
    onSave,
    onPublish,
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
  const isLiveOnStore = listing?.status === "synced";

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
              <Link href="/drafts">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back to drafts
              </Link>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="mt-1 h-11 w-11 shrink-0 md:hidden"
              asChild
            >
              <Link href="/drafts" aria-label="Back to drafts">
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
                <span
                  className={cn(
                    "inline-flex rounded-md border px-2 py-0.5 text-xs font-medium",
                    lifecycle === "Published" &&
                      "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
                    lifecycle === "Draft" && "border-border bg-muted text-foreground",
                    lifecycle === "Publish failed" &&
                      "border-destructive/30 bg-destructive/10 text-destructive",
                    lifecycle === "Publishing" &&
                      "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
                  )}
                  data-testid="product-lifecycle"
                >
                  {lifecycle}
                </span>
                <SaveStateIndicator
                  dirty={dirty}
                  saveState={saveState}
                  isLiveOnStore={isLiveOnStore}
                  onRetry={onSave}
                />
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

            <ProductEditorActions
              {...actionsProps}
              className="hidden shrink-0 lg:flex"
            />
            <div className="md:hidden">
              <ProductActionsMenu {...menuProps} />
            </div>
          </div>

          <ProductEditorActions
            {...actionsProps}
            className="hidden md:flex lg:hidden"
          />

          <ProductEditorTabs
            activeTab={activeTab}
            onChange={onTabChange}
            indicators={tabIndicators}
          />
        </div>
      </header>

      <MobileEditorActionBar
        saving={saving}
        saveDisabled={!dirty && saveState !== "error"}
        publishKind={publishKind}
        issueCount={issueCount}
        disabledPublishReason={disabledPublishReason}
        storefrontUrl={listing?.storefrontUrl}
        onPreview={onPreview}
        onSave={onSave}
        onPublish={onPublish}
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
