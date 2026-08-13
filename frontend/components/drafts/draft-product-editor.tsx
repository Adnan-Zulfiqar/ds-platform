"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

  // Optimistic-concurrency state (M2A, hardened in the acceptance pass).
  // `savedUpdatedAt` is the version token this editor last saw confirmed by
  // the server -- echoed back as `expectedUpdatedAt` on the next save so a
  // stale write is rejected (409) instead of silently overwriting a newer
  // change made elsewhere.
  const [savedUpdatedAt, setSavedUpdatedAt] = useState<string | null>(null);
  // `conflictPhase` replaces the earlier boolean `conflict` flag. The
  // acceptance pass found the previous "Keep my changes" recovery action
  // silently refreshed the version token and left autosave free to fire
  // immediately afterward -- a save could land moments later with no
  // review at all, which is not meaningfully different from the original
  // silent-overwrite bug this editor exists to prevent. The phases below
  // replace it with two safe, explicit paths:
  //   "detected"       -- 409 just happened; banner offers Reload or Review.
  //   "reload-confirm" -- merchant asked to reload; confirming the discard
  //                       before it happens (irreversible, so it is a
  //                       distinct, explicit step, not the same click).
  //   "reviewing"       -- merchant asked to review; both versions are shown
  //                       side by side and a second, separate action is
  //                       required to overwrite the server's newer value.
  // Autosave and manual Save are frozen for every phase except "none".
  const [conflictPhase, setConflictPhase] = useState<
    "none" | "detected" | "reload-confirm" | "reviewing"
  >("none");
  const isConflicted = conflictPhase !== "none";
  // The server's latest version, fetched when "Review my changes" opens --
  // captured separately from `data` so it can be shown next to the
  // merchant's still-untouched local fields without overwriting either.
  // Never written into any editable field automatically; only read for
  // display and as the version token for the one explicit overwrite action.
  const [conflictServerSnapshot, setConflictServerSnapshot] =
    useState<ProductDetail | null>(null);

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
   * so a confirmed reload would clear the conflict banner but leave the
   * stale local edits sitting in the fields, unreset. Only ever called from
   * an explicit merchant action (confirmed reload, or the normal initial
   * load) -- never automatically while a conflict is open, which is what
   * keeps "Review my changes" from silently discarding local edits. */
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
    applyDraftToForm(data);
    setSavedUpdatedAt(data.updatedAt);
    setConflictPhase("none");
    setConflictServerSnapshot(null);
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

  /** Every editable field, as it would be sent on save. Shared by the
   * normal save path and "Save my version anyway" so the two can never
   * drift -- the only thing that differs between them is which version
   * token they assert against. */
  function buildSavePayload(expectedUpdatedAt: string): ProductUpdatePayload {
    return {
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
      expectedUpdatedAt,
    };
  }

  async function handleSave(event?: FormEvent) {
    event?.preventDefault();
    // Guards against two hazards at once: a double-click or a keyboard
    // shortcut firing while a save is already in flight (no concurrent
    // duplicate requests), and autosave silently retrying over an
    // unresolved conflict -- the merchant must explicitly choose "Reload
    // latest version" or "Review my changes" first (see the conflict
    // banner below). `handleSaveMyVersionAnyway` is the one save path that
    // deliberately bypasses this guard, because it *is* the explicit,
    // reviewed choice this guard exists to require first.
    if (updateDraft.isPending || isConflicted) return;
    // The form only renders once `data` has loaded (see the early returns
    // below), and the `[data]` effect always sets this in the same tick --
    // reaching here without it would mean saving against no known version
    // at all, which the backend now rejects outright. Bail rather than
    // send a request guaranteed to 422.
    if (!savedUpdatedAt) return;

    setFormError(null);
    setSaveState("saving");

    try {
      const saved = await updateDraft.mutateAsync(buildSavePayload(savedUpdatedAt));
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
        // recovery here is "reload or review", not "fix a field and
        // retry", and the two must not look the same to the merchant.
        setConflictPhase("detected");
        setSaveState("error");
      } else {
        setSaveState("error");
        setFormError(err instanceof Error ? err.message : "Save failed.");
      }
    }
  }

  /** "Reload latest version", step 1 of 2 -- opens a confirmation dialog.
   * Discarding unsaved local edits is irreversible, so it is never a
   * single click from the conflict banner itself. */
  function handleRequestReload() {
    setConflictPhase("reload-confirm");
  }

  function handleCancelReloadConfirm() {
    setConflictPhase("detected");
  }

  /** "Reload latest version", step 2 of 2 (explicit confirmation given) --
   * discards local edits and adopts the server's current values. Applies
   * the refetched detail directly (see `applyDraftToForm`'s docstring for
   * why this can't be left to the `[data]` effect alone). */
  async function handleConfirmReload() {
    const result = await refetch();
    if (result.data) {
      applyDraftToForm(result.data);
      setSavedUpdatedAt(result.data.updatedAt);
    }
    setConflictServerSnapshot(null);
    setConflictPhase("none");
  }

  /** "Review my changes" -- fetches the latest server version and shows it
   * next to the merchant's still-untouched local fields. Does not touch
   * any editable field, does not change `savedUpdatedAt`, and does not
   * save anything: reviewing is inert by itself, exactly so it can never
   * be the thing that silently lets autosave through. */
  async function handleOpenReview() {
    const result = await refetch();
    if (result.data) {
      setConflictServerSnapshot(result.data);
      setConflictPhase("reviewing");
    }
  }

  function handleCancelReview() {
    setConflictServerSnapshot(null);
    setConflictPhase("detected");
  }

  /** The one explicit, deliberate action that overwrites the server's
   * newer value with the merchant's own -- in full, exactly as typed.
   * Never a per-field automatic merge: every field in the payload is the
   * merchant's own current value, chosen wholesale by this one click, not
   * assembled by combining fields from both versions.
   *
   * Uses the version token from the snapshot "Review my changes" fetched,
   * not `savedUpdatedAt` (which is still the original, now-stale value).
   * If the row moved again while the review was open, the server rejects
   * this exactly like any other stale save, and the merchant is returned
   * to the conflict banner rather than the save silently retrying. */
  async function handleSaveMyVersionAnyway() {
    if (!conflictServerSnapshot || updateDraft.isPending) return;

    setFormError(null);
    setSaveState("saving");

    try {
      const saved = await updateDraft.mutateAsync(
        buildSavePayload(conflictServerSnapshot.updatedAt),
      );
      setDirty(false);
      setSaveState("saved");
      setSavedUpdatedAt(saved.updatedAt);
      setConflictServerSnapshot(null);
      setConflictPhase("none");
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflictServerSnapshot(null);
        setConflictPhase("detected");
        setSaveState("error");
      } else {
        setSaveState("error");
        setFormError(err instanceof Error ? err.message : "Save failed.");
      }
    }
  }

  // Debounced autosave for merchant text fields. Frozen for every conflict
  // phase, not only while the top-level banner is showing -- autosave must
  // stay off while "Review my changes" or the reload confirmation is open
  // too, or it could save over a version the merchant hasn't finished
  // reviewing. (`handleSave` also guards this independently, but not
  // scheduling the timer at all avoids a pointless request-then-409 every
  // 1.8s.)
  useEffect(() => {
    if (!dirty || !data || isConflicted) return;
    const timer = window.setTimeout(() => {
      void handleSave();
    }, 1800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- autosave on dirty/conflictPhase only
  }, [dirty, conflictPhase, title, description, seoTitle, seoDescription, slug, tags]);

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

  // Every editable field where the merchant's local value differs from the
  // server's latest -- computed fresh on each render from the review
  // snapshot, never cached, so it can never show a stale comparison.
  const conflictDiffs = conflictServerSnapshot
    ? (
        [
          ["title", "Title", conflictServerSnapshot.title, title.trim()],
          ["brand", "Brand", conflictServerSnapshot.brand ?? "", brand.trim()],
          ["vendor", "Vendor", conflictServerSnapshot.vendor ?? "", vendor.trim()],
          [
            "categoryName",
            "Category",
            conflictServerSnapshot.categoryName ?? "",
            categoryName.trim(),
          ],
          ["tags", "Tags", (conflictServerSnapshot.tags ?? []).join(", "), tags.trim()],
          [
            "description",
            "Description",
            conflictServerSnapshot.description ?? "",
            description.trim(),
          ],
          ["seoTitle", "SEO title", conflictServerSnapshot.seoTitle ?? "", seoTitle.trim()],
          [
            "seoDescription",
            "SEO description",
            conflictServerSnapshot.seoDescription ?? "",
            seoDescription.trim(),
          ],
          ["slug", "URL slug", conflictServerSnapshot.slug ?? "", slug.trim()],
        ] as const
      )
        .filter(([, , serverValue, localValue]) => serverValue !== localValue)
        .map(([key, label, serverValue, localValue]) => ({ key, label, serverValue, localValue }))
    : [];

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

      {isConflicted ? (
        <Alert
          variant="destructive"
          role="alert"
          data-testid="draft-conflict-banner"
        >
          <AlertDescription className="space-y-3">
            <p>
              This draft was changed elsewhere since you opened it. Saving
              now would risk overwriting that change, so it was not applied,
              and autosave is paused until you choose what to do next.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleRequestReload}
                data-testid="conflict-reload-latest"
              >
                Reload latest version
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleOpenReview()}
                data-testid="conflict-review-mine"
              >
                Review my changes
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Reloading discards what you typed and replaces it with the
              latest saved version. Reviewing shows both versions side by
              side before you decide.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      <Dialog
        open={conflictPhase === "reload-confirm"}
        onOpenChange={(open) => {
          if (!open) handleCancelReloadConfirm();
        }}
      >
        <DialogContent data-testid="conflict-reload-confirm-dialog">
          <DialogHeader>
            <DialogTitle>Discard your changes and reload?</DialogTitle>
            <DialogDescription>
              This replaces every field below with the latest saved version.
              Anything you typed since opening this draft will be lost and
              cannot be recovered.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleCancelReloadConfirm}
              data-testid="conflict-reload-cancel"
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleConfirmReload()}
              data-testid="conflict-reload-confirm"
            >
              Discard my changes and reload
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={conflictPhase === "reviewing"}
        onOpenChange={(open) => {
          if (!open) handleCancelReview();
        }}
      >
        <DialogContent
          className="max-w-2xl"
          data-testid="conflict-review-dialog"
        >
          <DialogHeader>
            <DialogTitle>Review the conflicting changes</DialogTitle>
            <DialogDescription>
              {conflictDiffs.length > 0
                ? `${conflictDiffs.length} field${conflictDiffs.length === 1 ? "" : "s"} differ between the latest saved version and what you typed. Nothing is merged automatically.`
                : "The latest saved version and what you typed are now identical for every field shown here."}
            </DialogDescription>
          </DialogHeader>

          <div
            className="max-h-[50vh] space-y-4 overflow-y-auto"
            aria-live="polite"
          >
            {conflictDiffs.map((row) => (
              <div key={row.key} className="rounded-md border p-3">
                <p className="text-sm font-semibold">{row.label}</p>
                <div className="mt-2 grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">
                      Latest saved version
                    </p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-sm">
                      {row.serverValue || (
                        <span className="italic text-muted-foreground">Empty</span>
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">
                      Your unsaved version
                    </p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-sm">
                      {row.localValue || (
                        <span className="italic text-muted-foreground">Empty</span>
                      )}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <DialogFooter className="sm:flex-col sm:items-stretch sm:space-x-0 sm:space-y-2">
            <p className="text-xs text-muted-foreground">
              Saving your version overwrites the latest saved version above
              with exactly what you typed, field by field, with nothing
              combined automatically.
            </p>
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={handleCancelReview}
                data-testid="conflict-review-back"
              >
                Back
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={handleRequestReload}
                data-testid="conflict-review-reload-instead"
              >
                Reload latest version instead
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={updateDraft.isPending}
                onClick={() => void handleSaveMyVersionAnyway()}
                data-testid="conflict-save-mine-anyway"
              >
                {updateDraft.isPending ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : null}
                Save my version anyway
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
