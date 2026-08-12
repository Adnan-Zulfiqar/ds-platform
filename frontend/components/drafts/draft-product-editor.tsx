"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2, Store } from "lucide-react";

import { DraftInventoryPanel } from "@/components/drafts/draft-inventory-panel";
import { DraftMediaPanel } from "@/components/drafts/draft-media-panel";
import { DraftPostPublishPanel } from "@/components/drafts/draft-post-publish-panel";
import { DraftPricingPanel } from "@/components/drafts/draft-pricing-panel";
import { DraftSeoPanel } from "@/components/drafts/draft-seo-panel";
import { DraftShippingPanel } from "@/components/drafts/draft-shipping-panel";
import { DraftVariantsPanel } from "@/components/drafts/draft-variants-panel";
import { DraftPreviewPanel } from "@/components/drafts/draft-preview-panel";
import {
  ProductEditorHeader,
  ProductEditorHeaderSkeleton,
} from "@/components/drafts/editor-header/product-editor-header";
import {
  EDITOR_TAB_LABEL,
  isEditorTab,
  type EditorTab,
} from "@/components/drafts/editor-header/product-editor-tabs";
import { readinessFor } from "@/components/drafts/editor-header/readiness";
import { ProductVersionHistorySheet } from "@/components/products/product-version-history-sheet";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn, formatDateTime, formatMoney } from "@/lib/utils";
import {
  draftKeys,
  useDraft,
  useDraftListings,
  useDraftSeoScore,
  useRefreshDraft,
  useUpdateDraft,
} from "@/services/drafts";
import { useOptimizeProduct } from "@/services/products";
import { useStores } from "@/services/stores";
import { apiClient, ApiError } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import type {
  ProductDetail,
  ProductUpdatePayload,
  ShopifyPublishResult,
} from "@/types/api";

interface DraftProductEditorProps {
  productId: string;
}

/**
 * Premium draft product workspace — sticky header, inspector, autosave.
 */
export function DraftProductEditor({ productId }: DraftProductEditorProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const tabParam = useSearchParams().get("tab");
  const [tab, setTab] = useState<EditorTab>(
    isEditorTab(tabParam) ? tabParam : "overview",
  );

  const { data, isPending, isError, error, refetch } = useDraft(productId);
  const listingsQuery = useDraftListings(productId);
  const seoScoreQuery = useDraftSeoScore(productId);
  const updateDraft = useUpdateDraft(productId);
  const refreshDraft = useRefreshDraft(productId);
  const optimizeProduct = useOptimizeProduct(productId);
  const storesQuery = useStores({ size: 50 });

  const [title, setTitle] = useState("");
  const [brand, setBrand] = useState("");
  const [vendor, setVendor] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [tags, setTags] = useState("");
  const [description, setDescription] = useState("");
  const [seoTitle, setSeoTitle] = useState("");
  const [seoDescription, setSeoDescription] = useState("");
  const [slug, setSlug] = useState("");
  const [searchTopics, setSearchTopics] = useState("");
  const [primaryIntent, setPrimaryIntent] = useState("");
  const [primaryTopic, setPrimaryTopic] = useState("");
  const [redirectOldHandle, setRedirectOldHandle] = useState(true);
  const [ogTitle, setOgTitle] = useState("");
  const [ogDescription, setOgDescription] = useState("");
  const [dirty, setDirty] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  const [publishStoreId, setPublishStoreId] = useState("");
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishPending, setPublishPending] = useState(false);
  const [publishOk, setPublishOk] = useState<string | null>(null);
  const [publishResult, setPublishResult] =
    useState<ShopifyPublishResult | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  // Optimistic-concurrency state (M2A). `savedUpdatedAt` is the version
  // token this editor last saw confirmed by the server -- echoed back as
  // `expectedUpdatedAt` on the next save so a stale write is rejected
  // (409) instead of silently overwriting a newer change made elsewhere.
  const [savedUpdatedAt, setSavedUpdatedAt] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  // When a reload is triggered from "Keep my changes", the next `data`
  // load must refresh the version token and read-only context WITHOUT
  // clobbering the merchant's still-unsaved title/description -- the one
  // case where the load effect below must not reset form state.
  const preserveEditsOnNextLoad = useRef(false);

  function selectTab(next: EditorTab) {
    setTab(next);
    router.replace(`/drafts/${productId}?tab=${next}`);
  }

  /** Populate every merchant-editable field from a server `ProductDetail`,
   * and reset the dirty/save-state that goes with a fresh load.
   *
   * Called directly (not only from the `[data]` effect below) because
   * React Query's structural sharing means a refetch that comes back
   * byte-identical to what's already cached -- true whenever a rejected
   * conflicting write never actually persisted, which is exactly the
   * state right after a 409 -- leaves the `data` object reference
   * unchanged. An effect keyed on that reference would then never re-fire,
   * so "Reload latest version" would clear the conflict banner but leave
   * the stale local edits sitting in the fields, unreset. */
  function applyDraftToForm(detail: ProductDetail) {
    setTitle(detail.title);
    setBrand(detail.brand ?? "");
    setVendor(detail.vendor ?? "");
    setCategoryName(detail.categoryName ?? "");
    setTags(detail.tags.join(", "));
    setDescription(detail.description ?? "");
    setSeoTitle(detail.seoTitle ?? "");
    setSeoDescription(detail.seoDescription ?? "");
    setSlug(detail.slug ?? "");
    setSearchTopics((detail.searchTopics ?? []).join(", "));
    const planning = detail.seoPlanning ?? {};
    setPrimaryIntent(
      String(planning.primarySearchIntent ?? planning.primary_search_intent ?? ""),
    );
    setPrimaryTopic(String(planning.primaryTopic ?? planning.primary_topic ?? ""));
    setRedirectOldHandle(detail.redirectOldHandle ?? true);
    setOgTitle(detail.ogTitle ?? "");
    setOgDescription(detail.ogDescription ?? "");
    setDirty(false);
    setSaveState("idle");
  }

  useEffect(() => {
    if (!data) return;
    if (!preserveEditsOnNextLoad.current) {
      applyDraftToForm(data);
    }
    preserveEditsOnNextLoad.current = false;
    setSavedUpdatedAt(data.updatedAt);
    setConflict(false);
  }, [data]);

  useEffect(() => {
    if (isEditorTab(tabParam)) setTab(tabParam);
  }, [tabParam]);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  async function handleSave(event?: FormEvent) {
    event?.preventDefault();
    // Guards against two hazards at once: a double-click or a keyboard
    // shortcut firing while a save is already in flight (no concurrent
    // duplicate requests), and autosave silently retrying over an
    // unresolved conflict (the merchant must explicitly choose "Reload
    // latest" or "Keep my changes" first -- see the conflict banner below).
    if (updateDraft.isPending || conflict) return;

    setFormError(null);
    setSaveState("saving");

    const payload: ProductUpdatePayload = {
      title: title.trim(),
      brand: brand.trim() || null,
      vendor: vendor.trim() || null,
      categoryName: categoryName.trim() || null,
      tags: tags
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
      description: description,
      seoTitle: seoTitle.trim() || null,
      seoDescription: seoDescription.trim() || null,
      slug: slug.trim() || null,
      searchTopics: searchTopics
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
      seoPlanning: {
        primarySearchIntent: primaryIntent || null,
        primaryTopic: primaryTopic || null,
      },
      redirectOldHandle,
      ogTitle: ogTitle.trim() || null,
      ogDescription: ogDescription.trim() || null,
      ...(savedUpdatedAt ? { expectedUpdatedAt: savedUpdatedAt } : {}),
    };

    try {
      const saved = await updateDraft.mutateAsync(payload);
      setDirty(false);
      setSaveState("saved");
      // The server's response is authoritative: the next save's version
      // check is against what was *actually* persisted, not a value
      // computed client-side.
      setSavedUpdatedAt(saved.updatedAt);
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // A newer save landed elsewhere since this editor last loaded.
        // Surfaced as its own state, not folded into `formError` -- the
        // recovery here is "reload or keep your text", not "fix a field
        // and retry", and the two must not look the same to the merchant.
        setConflict(true);
        setSaveState("error");
      } else {
        setSaveState("error");
        setFormError(err instanceof Error ? err.message : "Save failed.");
      }
    }
  }

  /** "Reload latest version" -- discard local edits, adopt the server's
   * current values. Applies the refetched detail directly (see
   * `applyDraftToForm`'s docstring for why this can't be left to the
   * `[data]` effect alone). */
  async function handleReloadLatest() {
    preserveEditsOnNextLoad.current = false;
    const result = await refetch();
    if (result.data) {
      applyDraftToForm(result.data);
      setSavedUpdatedAt(result.data.updatedAt);
    }
    setConflict(false);
  }

  /** "Keep my changes" -- refresh the version token and read-only context
   * (supplier panel, listing state) without touching the merchant's
   * still-unsaved title/description, so a follow-up Save can succeed
   * against the current version instead of repeating the same conflict. */
  async function handleKeepMyChanges() {
    preserveEditsOnNextLoad.current = true;
    const result = await refetch();
    // Same reasoning as `handleReloadLatest` above -- must not depend on
    // the `data` reference having changed.
    if (result.data) setSavedUpdatedAt(result.data.updatedAt);
    setConflict(false);
  }

  // Debounced autosave for merchant text fields. Frozen while a conflict
  // is unresolved (`handleSave` also guards this, but not scheduling the
  // timer at all avoids a pointless request-then-409 every 1.8s).
  useEffect(() => {
    if (!dirty || !data || conflict) return;
    const timer = window.setTimeout(() => {
      void handleSave();
    }, 1800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- autosave on dirty/conflict only
  }, [dirty, conflict, title, description, seoTitle, seoDescription, slug, tags]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void handleSave();
      }
      if (meta && event.key.toLowerCase() === "p") {
        event.preventDefault();
        selectTab("publishing");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, title, description, seoTitle, seoDescription, slug, tags]);

  async function handlePublish() {
    setPublishError(null);
    setPublishOk(null);
    setPublishResult(null);
    if (!publishStoreId) {
      setPublishError("Select a connected Shopify store.");
      return;
    }
    setPublishPending(true);
    try {
      if (dirty) await handleSave();
      const { data: result } = await apiClient.post<ShopifyPublishResult>(
        "/integrations/shopify/publish",
        {
          productId,
          storeId: publishStoreId,
        },
      );
      setPublishResult(result);
      setPublishOk(result.message || "Publish completed.");
      void refetch();
      void queryClient.invalidateQueries({
        queryKey: draftKeys.listings(productId),
      });
    } catch (err) {
      setPublishError(
        err instanceof Error ? err.message : "Publish to Store failed.",
      );
    } finally {
      setPublishPending(false);
    }
  }

  if (isPending) {
    return (
      <div className="space-y-4">
        <ProductEditorHeaderSkeleton />
        <div className="h-64 animate-pulse rounded-lg border bg-muted/30" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <ErrorState
        title="Could not load draft"
        description={
          error instanceof Error ? error.message : "Please try again."
        }
        onRetry={() => void refetch()}
      />
    );
  }

  const readiness = readinessFor(data);
  const shopifyStores =
    storesQuery.data?.items.filter((store) => store.platform === "shopify") ??
    [];
  const syncedListing =
    listingsQuery.data?.find((row) => row.status === "synced") ??
    listingsQuery.data?.[0] ??
    null;

  return (
    <div className="space-y-4 pb-28 md:pb-6" data-testid="draft-editor">
      <ProductEditorHeader
        product={{ ...data, title: title || data.title }}
        activeTab={tab}
        onTabChange={selectTab}
        dirty={dirty}
        saveState={saveState}
        saving={updateDraft.isPending || saveState === "saving"}
        publishPending={publishPending}
        publishFailed={Boolean(publishError)}
        listing={syncedListing}
        seoScore={seoScoreQuery.data}
        readiness={readiness}
        refreshing={refreshDraft.isPending}
        optimizing={optimizeProduct.isPending}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => setInspectorOpen((open) => !open)}
        onPreview={() => setPreviewOpen(true)}
        onSave={() => void handleSave()}
        onPublish={() => selectTab("publishing")}
        onRefresh={() => void refreshDraft.mutateAsync()}
        onOptimize={() => optimizeProduct.mutate({})}
        onViewHistory={() => setHistoryOpen(true)}
      />

      <DraftPreviewPanel
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        product={data}
        title={title}
        description={description}
        seoTitle={seoTitle}
        seoDescription={seoDescription}
      />

      <ProductVersionHistorySheet
        productId={productId}
        productTitle={data.title}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        hideTrigger
      />

      {optimizeProduct.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {optimizeProduct.error instanceof Error
              ? optimizeProduct.error.message
              : "Optimization failed."}
          </AlertDescription>
        </Alert>
      ) : null}

      {conflict ? (
        <Alert
          variant="destructive"
          role="alert"
          data-testid="draft-conflict-banner"
        >
          <AlertDescription className="space-y-3">
            <p>
              This draft was changed elsewhere since you opened it. Saving
              now would risk overwriting that change, so it was not applied.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleReloadLatest}
                data-testid="conflict-reload-latest"
              >
                Reload latest version
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleKeepMyChanges}
                data-testid="conflict-keep-mine"
              >
                Keep my changes
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Reloading replaces the title and description below with the
              latest saved version. Keeping your changes leaves what you
              typed as-is and lets you save again against the current
              version.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 space-y-6">
          {tab === "overview" ? (
            <section className="space-y-4" aria-labelledby="overview-heading">
              <h2 id="overview-heading" className="text-lg font-semibold">
                Overview
              </h2>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2 md:col-span-2">
                  <Label htmlFor="draft-title">Title</Label>
                  <Input
                    id="draft-title"
                    value={title}
                    onChange={(event) => {
                      setTitle(event.target.value);
                      setDirty(true);
                    }}
                    data-testid="draft-title-input"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="draft-brand">Brand</Label>
                  <Input
                    id="draft-brand"
                    value={brand}
                    onChange={(event) => {
                      setBrand(event.target.value);
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="draft-vendor">Vendor</Label>
                  <Input
                    id="draft-vendor"
                    value={vendor}
                    onChange={(event) => {
                      setVendor(event.target.value);
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="draft-category">Category</Label>
                  <Input
                    id="draft-category"
                    value={categoryName}
                    onChange={(event) => {
                      setCategoryName(event.target.value);
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="draft-tags">Tags (comma-separated)</Label>
                  <Input
                    id="draft-tags"
                    value={tags}
                    onChange={(event) => {
                      setTags(event.target.value);
                      setDirty(true);
                    }}
                  />
                </div>
              </div>

              <div className="rounded-lg border bg-muted/20 p-4">
                <h3 className="mb-3 text-sm font-semibold">Supplier source</h3>
                <dl className="grid gap-2 text-sm md:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">AliExpress ID</dt>
                    <dd className="font-medium tabular-nums">{data.externalId}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Supplier</dt>
                    <dd className="font-medium">{data.supplierName ?? "—"}</dd>
                  </div>
                  <div className="md:col-span-2">
                    <dt className="text-muted-foreground">Original title</dt>
                    <dd className="font-medium">
                      {data.supplierTitle ?? data.title}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Supplier brand</dt>
                    <dd className="font-medium">
                      {data.supplierBrand ?? "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Source URL</dt>
                    <dd className="truncate font-medium">
                      {data.externalUrl ? (
                        <a
                          href={data.externalUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="underline-offset-4 hover:underline"
                        >
                          Open on AliExpress
                        </a>
                      ) : (
                        "—"
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Last refresh</dt>
                    <dd className="font-medium">
                      {formatDateTime(data.lastSyncedAt)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Supplier cost</dt>
                    <dd className="font-medium">
                      {formatMoney(data.costPriceMin, data.currency)}
                      {data.costPriceMax &&
                      data.costPriceMax !== data.costPriceMin
                        ? ` – ${data.costPriceMax}`
                        : ""}
                    </dd>
                  </div>
                </dl>
              </div>
            </section>
          ) : null}

          {tab === "description" ? (
            <section className="space-y-4" aria-labelledby="description-heading">
              <h2 id="description-heading" className="text-lg font-semibold">
                Description
              </h2>
              <p className="text-sm text-muted-foreground">
                Edit sanitized HTML for your listing. Supplier refresh will not
                overwrite this field once it differs from the supplier snapshot.
              </p>
              <div className="space-y-2">
                <Label htmlFor="draft-description">Merchant description</Label>
                <Textarea
                  id="draft-description"
                  className="min-h-[280px] font-mono text-sm"
                  value={description}
                  onChange={(event) => {
                    setDescription(event.target.value);
                    setDirty(true);
                  }}
                  data-testid="draft-description-input"
                />
              </div>
              <div className="rounded-lg border p-4">
                <h3 className="mb-2 text-sm font-semibold">HTML preview</h3>
                <div
                  className="prose prose-sm dark:prose-invert max-w-none"
                  data-testid="draft-description-preview"
                  // Sanitized on the server before storage; never raw supplier HTML.
                  dangerouslySetInnerHTML={{
                    __html: description || "<p class='text-muted-foreground'>No description yet.</p>",
                  }}
                />
              </div>
              {data.supplierDescription ? (
                <details className="rounded-lg border p-4">
                  <summary className="cursor-pointer text-sm font-semibold">
                    Supplier description snapshot
                  </summary>
                  <div
                    className="prose prose-sm dark:prose-invert mt-3 max-w-none opacity-80"
                    dangerouslySetInnerHTML={{
                      __html: data.supplierDescription,
                    }}
                  />
                </details>
              ) : null}
            </section>
          ) : null}

          {tab === "seo" ? (
            <DraftSeoPanel
              productId={productId}
              productTitle={title || data.title}
              seoTitle={seoTitle}
              seoDescription={seoDescription}
              slug={slug}
              tags={tags}
              searchTopics={searchTopics}
              primaryIntent={primaryIntent}
              primaryTopic={primaryTopic}
              redirectOldHandle={redirectOldHandle}
              ogTitle={ogTitle}
              ogDescription={ogDescription}
              onChange={(patch) => {
                if (patch.seoTitle !== undefined) setSeoTitle(patch.seoTitle);
                if (patch.seoDescription !== undefined)
                  setSeoDescription(patch.seoDescription);
                if (patch.slug !== undefined) setSlug(patch.slug);
                if (patch.tags !== undefined) setTags(patch.tags);
                if (patch.searchTopics !== undefined)
                  setSearchTopics(patch.searchTopics);
                if (patch.primaryIntent !== undefined)
                  setPrimaryIntent(patch.primaryIntent);
                if (patch.primaryTopic !== undefined)
                  setPrimaryTopic(patch.primaryTopic);
                if (patch.redirectOldHandle !== undefined)
                  setRedirectOldHandle(patch.redirectOldHandle);
                if (patch.ogTitle !== undefined) setOgTitle(patch.ogTitle);
                if (patch.ogDescription !== undefined)
                  setOgDescription(patch.ogDescription);
                setDirty(true);
              }}
            />
          ) : null}

          {tab === "publishing" ? (
            <section className="space-y-4" data-testid="publishing-panel">
              <h2 className="text-lg font-semibold">Publish to Store</h2>
              <p className="text-sm text-muted-foreground">
                Sends this prepared draft to a connected Shopify store. Import
                means supplier ingestion only — this action is channel
                publishing.
              </p>
              {(publishResult || syncedListing) && (
                <DraftPostPublishPanel
                  listing={syncedListing}
                  publishResult={publishResult}
                  onContinueEditing={() => selectTab("overview")}
                />
              )}
              {readiness.issues.length > 0 ? (
                <Alert>
                  <AlertDescription>
                    Readiness {readiness.score}/100 ({readiness.level}). Review
                    issues in the sidebar before publishing when possible.
                  </AlertDescription>
                </Alert>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="publish-store">Shopify store</Label>
                <select
                  id="publish-store"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={publishStoreId}
                  onChange={(event) => setPublishStoreId(event.target.value)}
                >
                  <option value="">Select a store…</option>
                  {shopifyStores.map((store) => (
                    <option key={store.id} value={store.id}>
                      {store.name}
                      {store.status !== "connected" ? ` (${store.status})` : ""}
                    </option>
                  ))}
                </select>
              </div>
              {publishError ? (
                <Alert variant="destructive">
                  <AlertDescription>{publishError}</AlertDescription>
                </Alert>
              ) : null}
              {publishOk ? (
                <Alert>
                  <AlertDescription>{publishOk}</AlertDescription>
                </Alert>
              ) : null}
              <Button
                disabled={publishPending}
                onClick={() => void handlePublish()}
                data-testid="publish-to-store"
              >
                {publishPending ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <Store className="mr-1.5 h-4 w-4" />
                )}
                Publish to Store
              </Button>
            </section>
          ) : null}

          {tab === "media" ? (
            <DraftMediaPanel productId={productId} product={data} />
          ) : null}

          {tab === "variants" ? (
            <DraftVariantsPanel productId={productId} product={data} />
          ) : null}

          {tab === "pricing" ? (
            <DraftPricingPanel productId={productId} product={data} />
          ) : null}

          {tab === "inventory" ? (
            <DraftInventoryPanel productId={productId} product={data} />
          ) : null}

          {tab === "shipping" ? (
            <DraftShippingPanel productId={productId} product={data} />
          ) : null}

          {tab !== "overview" &&
          tab !== "description" &&
          tab !== "seo" &&
          tab !== "publishing" &&
          tab !== "media" &&
          tab !== "variants" &&
          tab !== "pricing" &&
          tab !== "inventory" &&
          tab !== "shipping" ? (
            <section className="rounded-lg border border-dashed p-8 text-center">
              <h2 className="text-lg font-semibold">{EDITOR_TAB_LABEL[tab]}</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                {tab === "ai-studio" &&
                  "Use Optimize with AI from More actions for now. Side-by-side proposal studio is Stage 6."}
                {tab === "history" &&
                  "Use View History in More actions for AI version restore. Full edit timeline is Stage 6."}
              </p>
            </section>
          ) : null}
        </div>

        <aside
          className={cn(
            "space-y-4 xl:sticky xl:top-36 xl:self-start",
            !inspectorOpen && "hidden xl:hidden",
          )}
        >
          <div className="rounded-lg border p-4">
            <h3 className="text-sm font-semibold">Publish readiness</h3>
            <p className="mt-2 text-3xl font-semibold tabular-nums">
              {readiness.score}
              <span className="text-base font-normal text-muted-foreground">
                /100
              </span>
            </p>
            <Badge className="mt-2" variant="outline">
              {readiness.level}
            </Badge>
            {seoScoreQuery.data ? (
              <p className="mt-3 text-sm text-muted-foreground">
                SEO score {seoScoreQuery.data.score}/100 (
                {seoScoreQuery.data.status})
              </p>
            ) : null}
            {syncedListing ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Listing {syncedListing.status}
                {syncedListing.onlineStorePublished === false
                  ? " · not on Online Store"
                  : syncedListing.storefrontUrl
                    ? " · storefront URL verified"
                    : ""}
              </p>
            ) : null}
            {readiness.issues.length > 0 ? (
              <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
                {readiness.issues.map((issue) => (
                  <li key={issue}>• {issue}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-muted-foreground">
                Core content checks passed. Channel validation still runs at
                publish time.
              </p>
            )}
          </div>

          <div className="rounded-lg border p-4 text-sm">
            <h3 className="font-semibold">Supplier summary</h3>
            <dl className="mt-2 space-y-1 text-muted-foreground">
              <div className="flex justify-between gap-2">
                <dt>Stock (cached)</dt>
                <dd className="tabular-nums text-foreground">
                  {data.stockQuantity.toLocaleString()}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>Variants</dt>
                <dd className="tabular-nums text-foreground">
                  {data.variants.length}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>Images</dt>
                <dd className="tabular-nums text-foreground">
                  {data.images.length}
                </dd>
              </div>
            </dl>
          </div>
        </aside>
      </div>

    </div>
  );
}
