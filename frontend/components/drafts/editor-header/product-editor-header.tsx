"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { MobileEditorActionBar } from "@/components/drafts/editor-header/mobile-editor-action-bar";
import { ProductActionsMenu } from "@/components/drafts/editor-header/product-actions-menu";
import { ProductEditorActions } from "@/components/drafts/editor-header/product-editor-actions";
import { ProductEditorBreadcrumb } from "@/components/drafts/editor-header/product-editor-breadcrumb";
import {
  ProductEditorTabs,
  type EditorTab,
} from "@/components/drafts/editor-header/product-editor-tabs";
import { ProductIdentity } from "@/components/drafts/editor-header/product-identity";
import { ProductMetrics } from "@/components/drafts/editor-header/product-metrics";
import {
  ProductStatusGroup,
  type LifecycleBadge,
} from "@/components/drafts/editor-header/product-status-group";
import type { PublishActionKind } from "@/components/drafts/editor-header/publish-action";
import {
  estimateMarginPercent,
  type ReadinessSummary,
} from "@/components/drafts/editor-header/readiness";
import {
  SaveStateIndicator,
  type SaveState,
} from "@/components/drafts/editor-header/save-state-indicator";
import {
  deriveSupplierSyncKind,
  SupplierSyncStatus,
} from "@/components/drafts/editor-header/supplier-sync-status";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProductDetail, SeoScore, StoreListing } from "@/types/api";

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
  readiness: ReadinessSummary;
}): LifecycleBadge {
  if (params.productStatus === "archived") return "Archived";
  if (params.publishPending) return "Publishing";
  if (params.publishFailed || params.listing?.status === "error") {
    return "Publish Failed";
  }
  if (params.listing?.status === "synced") return "Published";
  if (params.readiness.level === "Ready") return "Ready";
  if (params.readiness.level === "Needs Review") return "Needs Review";
  return "Draft";
}

function derivePublishKind(params: {
  publishPending: boolean;
  publishFailed: boolean;
  listing: StoreListing | null;
  dirty: boolean;
  issueCount: number;
}): PublishActionKind {
  if (params.publishPending) return "publishing";
  if (params.publishFailed || params.listing?.status === "error") return "retry";
  if (params.listing?.status === "synced") {
    return params.dirty ? "push_updates" : "view_store";
  }
  if (params.issueCount > 0) return "fix_issues";
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
    readiness,
  });
  const publishKind = derivePublishKind({
    publishPending,
    publishFailed,
    listing,
    dirty,
    issueCount: readiness.issues.length,
  });
  const supplierKind = deriveSupplierSyncKind({
    lastSyncedAt: product.lastSyncedAt,
    lastSyncError: product.lastSyncError,
    refreshing,
  });
  const margin = estimateMarginPercent(
    product.sellPrice,
    product.costPriceMin,
  );
  const shopifyLabel = listing
    ? listing.status === "synced"
      ? "Connected"
      : listing.status === "error"
        ? "Failed"
        : "Pending"
    : "Not connected";
  const publicationLabel = listing
    ? listing.status === "synced"
      ? listing.onlineStorePublished === false
        ? "Published (unpublished on storefront)"
        : "Published to Shopify"
      : listing.status === "error"
        ? "Publish failed"
        : "Out of sync"
    : "Not published";

  const disabledPublishReason =
    publishKind === "fix_issues"
      ? readiness.issues.slice(0, 3).join(" · ")
      : null;

  const openAliExpress = () => {
    if (product.externalUrl) {
      window.open(product.externalUrl, "_blank", "noopener,noreferrer");
    }
  };

  const menuProps = {
    refreshing,
    optimizing,
    hasExternalUrl: Boolean(product.externalUrl),
    onRefresh,
    onOptimize,
    onOpenAliExpress: openAliExpress,
    onViewHistory,
    onGoHistoryTab: () => onTabChange("history"),
  };

  const actionsProps = {
    saving,
    saveDisabled: !dirty && saveState !== "error",
    dirty,
    publishKind,
    issueCount: readiness.issues.length,
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
    publishing: publishKind === "fix_issues" ? { blocked: true } : undefined,
  };

  return (
    <>
      <header
        className="sticky top-0 z-20 -mx-1 border-b border-border/80 bg-background/95 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/85"
        data-testid="product-editor-header"
      >
        <div className="space-y-3 px-1 py-3 md:px-2">
          {/* Layer 1 — context */}
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="h-11 w-11 shrink-0 md:hidden"
              asChild
            >
              <Link href="/drafts" aria-label="Back to Drafts">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              </Link>
            </Button>
            <ProductEditorBreadcrumb className="hidden min-w-0 flex-1 sm:block" />
            <p className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground sm:hidden">
              Edit draft
            </p>
            <div className="ml-auto flex items-center gap-2 sm:gap-3">
              <SaveStateIndicator dirty={dirty} saveState={saveState} />
              <div className="md:hidden">
                <ProductActionsMenu {...menuProps} />
              </div>
              <button
                type="button"
                className="hidden text-xs text-muted-foreground underline-offset-2 hover:underline lg:inline"
                onClick={onToggleInspector}
              >
                {inspectorOpen ? "Hide inspector" : "Show inspector"}
              </button>
            </div>
          </div>

          {/* Layer 2 — identity + actions */}
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
            <ProductIdentity
              title={product.title}
              externalId={product.externalId}
              supplierName={product.supplierName}
              externalUrl={product.externalUrl}
              thumbnailUrl={featuredImage}
            >
              <SupplierSyncStatus
                kind={supplierKind}
                lastSyncedAt={product.lastSyncedAt}
                shipToCountry={product.shipToCountry}
              />
              <ProductStatusGroup
                lifecycle={lifecycle}
                readiness={readiness}
                seoScore={seoScore?.score}
                seoStatus={seoScore?.status}
                publicationLabel={publicationLabel}
                onReadinessClick={onToggleInspector}
                onSeoClick={() => onTabChange("seo")}
              />
              <ProductMetrics
                readinessScore={readiness.score}
                seoScore={null}
                marginPercent={margin}
                shopifyLabel={shopifyLabel}
                onReadinessClick={undefined}
                onSeoClick={undefined}
                onMarginClick={() => onTabChange("pricing")}
                onShopifyClick={() => onTabChange("publishing")}
                className="hidden pt-0.5 sm:flex"
              />
            </ProductIdentity>

            {/* Desktop: actions beside identity */}
            <ProductEditorActions
              {...actionsProps}
              className="hidden lg:flex"
            />
          </div>

          {/* Tablet: actions under identity, above tabs */}
          <ProductEditorActions
            {...actionsProps}
            className="hidden md:flex lg:hidden"
          />

          {/* Layer 3 — tabs */}
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
        issueCount={readiness.issues.length}
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
      <Skeleton className="h-4 w-64" />
      <div className="flex gap-3">
        <Skeleton className="h-[72px] w-[72px] rounded-lg" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-7 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-5 w-40" />
        </div>
      </div>
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
