"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  Loader2,
  RefreshCw,
  Save,
  Store,
} from "lucide-react";

import { DraftInventoryPanel } from "@/components/drafts/draft-inventory-panel";
import { DraftMediaPanel } from "@/components/drafts/draft-media-panel";
import { DraftPostPublishPanel } from "@/components/drafts/draft-post-publish-panel";
import { DraftPricingPanel } from "@/components/drafts/draft-pricing-panel";
import { DraftSeoPanel } from "@/components/drafts/draft-seo-panel";
import { DraftShippingPanel } from "@/components/drafts/draft-shipping-panel";
import { DraftVariantsPanel } from "@/components/drafts/draft-variants-panel";
import { OptimizeProductButton } from "@/components/products/optimize-product-button";
import { ProductVersionHistorySheet } from "@/components/products/product-version-history-sheet";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
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
import { useStores } from "@/services/stores";
import { apiClient } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import type {
  ProductDetail,
  ProductUpdatePayload,
  ShopifyPublishResult,
} from "@/types/api";

const TABS = [
  "overview",
  "description",
  "media",
  "variants",
  "pricing",
  "inventory",
  "shipping",
  "seo",
  "ai-studio",
  "publishing",
  "history",
] as const;

type EditorTab = (typeof TABS)[number];

const TAB_LABEL: Record<EditorTab, string> = {
  overview: "Overview",
  description: "Description",
  media: "Media",
  variants: "Variants",
  pricing: "Pricing",
  inventory: "Inventory",
  shipping: "Shipping",
  seo: "SEO",
  "ai-studio": "AI Studio",
  publishing: "Publishing",
  history: "History",
};

function isEditorTab(value: string | null): value is EditorTab {
  return value !== null && (TABS as readonly string[]).includes(value);
}

function readinessFor(product: ProductDetail): {
  score: number;
  level: string;
  issues: string[];
} {
  const issues: string[] = [];
  let score = 0;

  if (product.title.trim().length >= 8) score += 20;
  else issues.push("Title is too short");

  if (product.description && product.description.replace(/<[^>]+>/g, "").trim())
    score += 20;
  else issues.push("Description is missing");

  if (product.images.length > 0) score += 15;
  else issues.push("Add at least one image");

  if (product.variants.length > 0) score += 15;
  else issues.push("No variants imported");

  if (product.costPriceMin) score += 10;
  else issues.push("Supplier cost missing");

  if (product.seoTitle) score += 10;
  else issues.push("SEO title missing");

  if (product.slug) score += 10;
  else issues.push("URL slug missing");

  const level =
    issues.length === 0
      ? "Ready"
      : score >= 60
        ? "Needs Review"
        : score >= 30
          ? "Incomplete"
          : "Blocked";

  return { score, level, issues };
}

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

  useEffect(() => {
    if (!data) return;
    setTitle(data.title);
    setBrand(data.brand ?? "");
    setVendor(data.vendor ?? "");
    setCategoryName(data.categoryName ?? "");
    setTags(data.tags.join(", "));
    setDescription(data.description ?? "");
    setSeoTitle(data.seoTitle ?? "");
    setSeoDescription(data.seoDescription ?? "");
    setSlug(data.slug ?? "");
    setSearchTopics((data.searchTopics ?? []).join(", "));
    const planning = data.seoPlanning ?? {};
    setPrimaryIntent(
      String(planning.primarySearchIntent ?? planning.primary_search_intent ?? ""),
    );
    setPrimaryTopic(
      String(planning.primaryTopic ?? planning.primary_topic ?? ""),
    );
    setRedirectOldHandle(data.redirectOldHandle ?? true);
    setOgTitle(data.ogTitle ?? "");
    setOgDescription(data.ogDescription ?? "");
    setDirty(false);
    setSaveState("idle");
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
    };

    try {
      await updateDraft.mutateAsync(payload);
      setDirty(false);
      setSaveState("saved");
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
    } catch (err) {
      setSaveState("error");
      setFormError(err instanceof Error ? err.message : "Save failed.");
    }
  }

  // Debounced autosave for merchant text fields.
  useEffect(() => {
    if (!dirty || !data) return;
    const timer = window.setTimeout(() => {
      void handleSave();
    }, 1800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- autosave on dirty only
  }, [dirty, title, description, seoTitle, seoDescription, slug, tags]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void handleSave();
      }
      if (meta && event.key.toLowerCase() === "p") {
        event.preventDefault();
        setTab("publishing");
        router.replace(`/drafts/${productId}?tab=publishing`);
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
      <div className="space-y-4" data-testid="draft-editor-loading">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
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
  const featuredImage = data.images[0]?.url ?? null;
  const syncedListing =
    listingsQuery.data?.find((row) => row.status === "synced") ??
    listingsQuery.data?.[0] ??
    null;
  const publicationStatus = syncedListing
    ? syncedListing.onlineStorePublished === false
      ? "Published"
      : "Published"
    : publishPending
      ? "Publishing"
      : readiness.level === "Blocked"
        ? "Blocked"
        : readiness.level === "Ready"
          ? "Ready"
          : "Draft";

  return (
    <div className="space-y-4 pb-24 md:pb-6" data-testid="draft-editor">
      <div className="sticky top-0 z-20 -mx-1 space-y-3 border-b bg-background/95 px-1 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/drafts">
              <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Back to Drafts
            </Link>
          </Button>
          <Badge variant="secondary">{publicationStatus}</Badge>
          <Badge variant="outline">{data.status}</Badge>
          <span className="text-xs text-muted-foreground">
            Supplier sync:{" "}
            {data.lastSyncedAt
              ? formatDateTime(data.lastSyncedAt)
              : "never refreshed"}
          </span>
          <span
            className={cn(
              "text-xs font-medium",
              dirty ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
            )}
            data-testid="draft-save-state"
          >
            {saveState === "saving"
              ? "Autosaving…"
              : dirty
                ? "Unsaved changes"
                : saveState === "saved"
                  ? "Saved"
                  : "Up to date"}
          </span>
          <button
            type="button"
            className="ml-auto text-xs text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => setInspectorOpen((open) => !open)}
          >
            {inspectorOpen ? "Hide inspector" : "Show inspector"}
          </button>
        </div>

        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            {featuredImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={featuredImage}
                alt=""
                className="h-14 w-14 shrink-0 rounded-md border object-cover"
              />
            ) : (
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md border bg-muted text-xs text-muted-foreground">
                No img
              </div>
            )}
            <div className="min-w-0 flex-1 space-y-1">
              <Input
                aria-label="Product title"
                className="h-auto border-0 px-0 text-xl font-semibold tracking-tight shadow-none focus-visible:ring-1 md:text-2xl"
                value={title}
                onChange={(event) => {
                  setTitle(event.target.value);
                  setDirty(true);
                }}
                data-testid="draft-title-input"
              />
              <p className="text-sm text-muted-foreground">
                AliExpress {data.externalId}
                {data.supplierName ? ` · ${data.supplierName}` : ""}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={refreshDraft.isPending}
              onClick={() => void refreshDraft.mutateAsync()}
            >
              {refreshDraft.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-4 w-4" />
              )}
              Refresh
            </Button>
            <OptimizeProductButton productId={productId} />
            <ProductVersionHistorySheet
              productId={productId}
              productTitle={data.title}
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setTab("description");
                router.replace(`/drafts/${productId}?tab=description`);
              }}
            >
              Preview
            </Button>
            <Button
              size="sm"
              disabled={updateDraft.isPending}
              onClick={() => void handleSave()}
              data-testid="save-draft"
            >
              {updateDraft.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-1.5 h-4 w-4" />
              )}
              Save Draft
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setTab("publishing");
                router.replace(`/drafts/${productId}?tab=publishing`);
              }}
            >
              <Store className="mr-1.5 h-4 w-4" />
              Publish to Store
            </Button>
          </div>
        </div>

        <nav
          aria-label="Editor sections"
          className="flex gap-1 overflow-x-auto pb-1"
        >
          {TABS.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => {
                setTab(id);
                router.replace(`/drafts/${productId}?tab=${id}`);
              }}
              className={cn(
                "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                tab === id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
              data-testid={`editor-tab-${id}`}
            >
              {TAB_LABEL[id]}
            </button>
          ))}
        </nav>
      </div>

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
                  onContinueEditing={() => setTab("overview")}
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
              <h2 className="text-lg font-semibold">{TAB_LABEL[tab]}</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                {tab === "ai-studio" &&
                  "Use Optimize with AI in the header for now. Side-by-side proposal studio is Stage 6."}
                {tab === "history" &&
                  "Open History in the header for AI version restore. Full edit timeline is Stage 6."}
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

      <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 p-3 backdrop-blur md:hidden">
        <div className="flex gap-2">
          <Button
            className="flex-1"
            disabled={updateDraft.isPending || !dirty}
            onClick={() => void handleSave()}
          >
            Save Draft
          </Button>
          <Button
            className="flex-1"
            variant="secondary"
            onClick={() => {
              setTab("publishing");
              router.replace(`/drafts/${productId}?tab=publishing`);
            }}
          >
            Publish
          </Button>
        </div>
      </div>
    </div>
  );
}
