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
import type { SaveState } from "@/components/drafts/editor-header/save-state-indicator";
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

/** The merchant-editable text of the editor, as plain strings.
 *
 * Deliberately not a `ProductDetail`: this is the *merchant's* side of a
 * conflict, which only ever exists as form values, and typing it as the
 * server's shape would invite code that treats the two as interchangeable.
 * They are not -- keeping them distinct types is what makes an accidental
 * "apply the server's version to the form" a compile error rather than a
 * data-loss bug.
 */
interface EditableSnapshot {
  title: string;
  brand: string;
  vendor: string;
  categoryName: string;
  tags: string;
  description: string;
  seoTitle: string;
  seoDescription: string;
  slug: string;
  // Not surfaced in the review comparison (they are secondary SEO fields),
  // but captured all the same: a save payload must be *entirely* derivable
  // from one snapshot, or "what the dialog showed" and "what was sent"
  // can drift again through a field nobody was looking at.
  searchTopics: string;
  primaryIntent: string;
  primaryTopic: string;
  redirectOldHandle: boolean;
  ogTitle: string;
  ogDescription: string;
}

/** The two versions a merchant is being asked to choose between, frozen
 * together at the moment Review opens.
 *
 * One object, not two pieces of state, so the merchant's side and the
 * server's side can never come from different moments — the save asserts
 * `server.updatedAt` and sends `local`, and both were shown on screen
 * together. */
interface ReviewSnapshot {
  local: EditableSnapshot;
  server: ProductDetail;
}

/** Field-by-field equality over everything a save would send. */
function sameEditableValues(a: EditableSnapshot, b: EditableSnapshot): boolean {
  return (Object.keys(a) as Array<keyof EditableSnapshot>).every(
    (key) => a[key] === b[key],
  );
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
  // Uses the indicator's own `SaveState` rather than restating the union
  // here. The two had already drifted: the indicator has rendered a
  // "Conflict detected" state since the acceptance pass, but this local
  // copy of the type omitted "conflict", so nothing could ever set it and
  // a real 409 displayed the generic "Save failed" instead.
  const [saveState, setSaveState] = useState<SaveState>("idle");
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
  // The server's latest version as of the moment the conflict was detected.
  // Captured separately from `data` so it can be shown next to the
  // merchant's still-untouched local fields without overwriting either.
  // Never written into any editable field automatically; only read for
  // display and as the version token for the one explicit overwrite action.
  const [conflictServerSnapshot, setConflictServerSnapshot] =
    useState<ProductDetail | null>(null);
  // The merchant's own unsaved values, frozen at the instant the 409 came
  // back: a record of what the server actually rejected. Deliberately *not*
  // what the review screen shows or what an override sends -- the merchant
  // may legitimately keep typing after the conflict (autosave is paused,
  // editing is not), and saving this frozen copy would silently discard
  // that later work. Diagnostic/audit only.
  const [conflictLocalSnapshot, setConflictLocalSnapshot] =
    useState<EditableSnapshot | null>(null);
  // What the merchant is actually being asked to consent to: their values
  // and the server's, captured together when Review opens.
  //
  // The integration pass found the review dialog rendering the *conflict*
  // snapshot while the override rebuilt its payload from live form state,
  // so a merchant who typed after the 409 was shown one value and saved
  // another. Both sides now come from this one object, and the override
  // refuses to run if the form has moved since it was taken -- so the
  // dialog and the PATCH body are the same values by construction.
  const [reviewSnapshot, setReviewSnapshot] = useState<ReviewSnapshot | null>(
    null,
  );
  // Set when the form is found to have changed after Review opened. Blocks
  // the override until the merchant sees a refreshed comparison.
  const [reviewOutOfDate, setReviewOutOfDate] = useState(false);
  // The token the rejected save asserted against. Kept for diagnosis and to
  // make it explicit that the stale token is never silently reused.
  const [conflictStaleToken, setConflictStaleToken] = useState<string | null>(
    null,
  );

  // Which draft the form has already been hydrated from.
  //
  // This is the load-bearing guard for the whole editor. React Query hands
  // back a new `data` reference on every refetch that returns materially
  // different bytes -- and `useUpdateDraft` invalidates this very query on
  // every successful save, so refetches are routine, not exotic. Hydrating
  // whenever that reference changes is what silently destroyed unsaved
  // merchant work during conflict review (see M2_PREMIUM_EDITOR.md).
  //
  // A ref, not state: the decision has to be made synchronously inside the
  // effect, and re-rendering on it would be pointless churn.
  const hydratedFromRef = useRef<string | null>(null);

  function selectTab(next: EditorTab) {
    setTab(next);
    router.replace(`/drafts/${productId}?tab=${next}`);
  }

  /** Snapshot the merchant's current editable values.
   *
   * Read at exactly one moment -- when a 409 comes back -- so the review
   * screen can show what the server rejected rather than whatever happens
   * to be in the inputs by the time the merchant opens it. */
  function captureEditableSnapshot(): EditableSnapshot {
    return {
      title,
      brand,
      vendor,
      categoryName,
      tags,
      description,
      seoTitle,
      seoDescription,
      slug,
      searchTopics,
      primaryIntent,
      primaryTopic,
      redirectOldHandle,
      ogTitle,
      ogDescription,
    };
  }

  /** Overwrite every merchant-editable field with a server `ProductDetail`
   * and reset the dirty/save state that goes with a fresh baseline.
   *
   * **This function destroys unsaved merchant input.** It must only ever be
   * reached from one of the explicitly safe transitions enumerated in
   * `hydrateFromServer` -- never from a bare `data`-changed effect. */
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

  /** Adopt a server version as the editor's new baseline: its values become
   * the form, its `updatedAt` becomes the active concurrency token.
   *
   * The *only* three safe transitions, all of them explicit:
   *   1. the first successful load of a draft (nothing to lose yet);
   *   2. a confirmed "Reload latest version" (merchant chose to discard);
   *   3. — reserved — any future transition must be added here deliberately,
   *      with the same "is there unsaved work?" question answered first.
   *
   * Note what is *not* on that list: "React Query gave us a new object".
   * Structural sharing is a rendering optimisation, not a safety property.
   * It says nothing about whether the merchant has unsaved work, and
   * relying on it to keep a refetch from clobbering the form is how the
   * conflict-review data-loss defect happened. */
  function hydrateFromServer(detail: ProductDetail) {
    applyDraftToForm(detail);
    setSavedUpdatedAt(detail.updatedAt);
    hydratedFromRef.current = detail.id;
  }

  useEffect(() => {
    if (!data) return;
    // Transition 1 only. Every later refetch of this same draft -- the
    // post-save invalidation, a reconnect, an explicit `refetch()` for a
    // conflict snapshot -- is deliberately inert here. Nothing but an
    // explicit merchant action may replace what is in the form, and
    // nothing but an explicit merchant action may resolve a conflict.
    if (hydratedFromRef.current === data.id) return;
    hydrateFromServer(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- first-load hydration only
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

  /** Build the save payload from an explicit set of values.
   *
   * Takes a snapshot rather than reading the live form on purpose. The
   * normal save passes the current values; "Save my version anyway" passes
   * the exact values the review dialog rendered. Because there is only one
   * builder and it has no access to anything but its argument, "what was
   * shown" and "what was sent" cannot diverge — which is precisely how the
   * consent mismatch happened when this read component state directly. */
  function buildSavePayload(
    values: EditableSnapshot,
    expectedUpdatedAt: string,
  ): ProductUpdatePayload {
    return {
      title: values.title.trim(),
      brand: values.brand.trim() || null,
      vendor: values.vendor.trim() || null,
      categoryName: values.categoryName.trim() || null,
      tags: values.tags
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
      description: values.description,
      seoTitle: values.seoTitle.trim() || null,
      seoDescription: values.seoDescription.trim() || null,
      slug: values.slug.trim() || null,
      searchTopics: values.searchTopics
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean),
      seoPlanning: {
        primarySearchIntent: values.primaryIntent || null,
        primaryTopic: values.primaryTopic || null,
      },
      redirectOldHandle: values.redirectOldHandle,
      ogTitle: values.ogTitle.trim() || null,
      ogDescription: values.ogDescription.trim() || null,
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
      const saved = await updateDraft.mutateAsync(
        buildSavePayload(captureEditableSnapshot(), savedUpdatedAt),
      );
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
        await enterConflict(savedUpdatedAt);
      } else {
        setSaveState("error");
        setFormError(err instanceof Error ? err.message : "Save failed.");
      }
    }
  }

  /** Enter (or re-enter) conflict resolution after a real 409.
   *
   * Freezes saving, preserves the merchant's side, and fetches the
   * server's side for comparison. The fetch goes through `apiClient`
   * directly rather than `refetch()` on purpose: it must not write to the
   * query cache at all, so there is no path by which looking at the
   * server's version can disturb what the merchant is editing.
   *
   * `dirty` is deliberately left `true` -- the merchant's work genuinely
   * is unsaved, and the header must keep saying so. */
  async function enterConflict(staleToken: string | null) {
    setConflictLocalSnapshot(captureEditableSnapshot());
    setConflictStaleToken(staleToken);
    setSaveState("conflict");
    setConflictPhase("detected");

    try {
      const { data: latest } = await apiClient.get<ProductDetail>(
        `/drafts/${productId}`,
      );
      setConflictServerSnapshot(latest);
    } catch {
      // The banner and both recovery actions still work without it --
      // "Review my changes" retries the fetch, and "Reload latest version"
      // fetches its own copy. Nothing here is allowed to touch the form.
      setConflictServerSnapshot(null);
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
    // Transition 2: the merchant has explicitly chosen to lose their work,
    // so this is the one recovery path allowed to overwrite the form.
    // `refetch()` here (rather than a bare `apiClient` read) is correct --
    // we *are* adopting the server's version, so the shared cache should
    // hold it too.
    const result = await refetch();
    const latest = result.data ?? conflictServerSnapshot;
    if (latest) hydrateFromServer(latest);
    clearConflict();
    setSaveState("idle");
  }

  /** Reset every scrap of conflict state at once, so no half-resolved
   * combination (banner closed but snapshot retained, or vice versa) can
   * be reached by forgetting one setter at one call site. */
  function clearConflict() {
    setConflictPhase("none");
    setConflictServerSnapshot(null);
    setConflictLocalSnapshot(null);
    setConflictStaleToken(null);
    setReviewSnapshot(null);
    setReviewOutOfDate(false);
  }

  /** "Review my changes" -- freezes the pair of versions the merchant is
   * being asked to choose between, and shows them.
   *
   * The merchant's side is captured **now**, not when the 409 landed.
   * Editing stays enabled during a conflict (only saving is frozen), so by
   * the time Review is opened the merchant may legitimately have typed
   * more, and that later text is what they mean by "my version". Showing
   * the 409-time copy would misrepresent it; *saving* the 409-time copy
   * would silently discard it. Both are wrong, so neither is used: consent
   * begins here, and this is the moment that gets frozen.
   *
   * Reviewing remains inert. It does not write a field, clear `dirty`,
   * clear the conflict, touch the active token, or refetch the draft
   * query -- the server side was already fetched when the conflict was
   * entered, and the fetch below is only a retry for when that failed. */
  async function handleOpenReview() {
    let server = conflictServerSnapshot;
    if (!server) {
      try {
        const { data: latest } = await apiClient.get<ProductDetail>(
          `/drafts/${productId}`,
        );
        setConflictServerSnapshot(latest);
        server = latest;
      } catch (err) {
        setFormError(
          err instanceof Error
            ? err.message
            : "Could not load the latest saved version.",
        );
        return;
      }
    }
    setReviewSnapshot({ local: captureEditableSnapshot(), server });
    setReviewOutOfDate(false);
    setConflictPhase("reviewing");
  }

  /** Close the comparison and go back to the banner.
   *
   * Discards the review snapshot deliberately: a comparison the merchant
   * walked away from must not be able to authorise a later save. Reopening
   * Review captures whatever they have typed since. Local edits, the
   * conflict, and the token are all untouched -- closing a comparison is
   * not a decision. */
  function handleCancelReview() {
    setReviewSnapshot(null);
    setReviewOutOfDate(false);
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
    // Only a live review can authorise this. Without one there is nothing
    // the merchant has been shown, and therefore nothing they consented to.
    if (!reviewSnapshot || updateDraft.isPending) return;

    // The form can still change while the dialog is open -- a background
    // script, an autofill, a stray keystroke on a field behind the modal.
    // Rather than trust that it cannot, check: if the editor no longer
    // holds what the merchant was shown, refuse and make them look again.
    // Silently saving the newer values would reintroduce exactly the
    // consent mismatch this guard exists to close; silently saving the
    // reviewed ones would discard real work.
    if (!sameEditableValues(captureEditableSnapshot(), reviewSnapshot.local)) {
      setReviewOutOfDate(true);
      return;
    }

    setFormError(null);
    setSaveState("saving");

    try {
      // Exactly the values the dialog rendered, against exactly the version
      // it named. Both come out of the same frozen object.
      const saved = await updateDraft.mutateAsync(
        buildSavePayload(reviewSnapshot.local, reviewSnapshot.server.updatedAt),
      );
      // The merchant's values won and are now the persisted truth. The
      // response is authoritative for the new baseline -- but note it is
      // only the *token* and dirty flag that move here, never the form
      // fields: those already hold exactly what was sent.
      setDirty(false);
      setSaveState("saved");
      setSavedUpdatedAt(saved.updatedAt);
      hydratedFromRef.current = saved.id;
      clearConflict();
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // The row moved again between the review and this override. The
        // merchant's values are still in the form and still theirs --
        // re-enter conflict resolution against the newer server version
        // rather than retrying, which would be an unbounded overwrite
        // race against whoever else is editing. `enterConflict` clears the
        // review snapshot's authority by returning to "detected", so the
        // next override needs a fresh, re-read comparison.
        setReviewSnapshot(null);
        setReviewOutOfDate(false);
        await enterConflict(reviewSnapshot.server.updatedAt);
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

  // Every editable field where the merchant's rejected value differs from
  // the server's latest. Both sides come from the snapshots frozen when the
  // 409 landed -- never from the live form or the live query -- so the
  // comparison shows exactly the two versions that actually collided, and
  // cannot drift under a background refetch or continued typing.
  const reviewedServer = reviewSnapshot?.server ?? null;
  const reviewedLocal = reviewSnapshot?.local ?? null;
  const conflictDiffs =
    reviewedServer && reviewedLocal
      ? (
          [
            ["title", "Title", reviewedServer.title, reviewedLocal.title],
            ["brand", "Brand", reviewedServer.brand ?? "", reviewedLocal.brand],
            [
              "vendor",
              "Vendor",
              reviewedServer.vendor ?? "",
              reviewedLocal.vendor,
            ],
            [
              "categoryName",
              "Category",
              reviewedServer.categoryName ?? "",
              reviewedLocal.categoryName,
            ],
            [
              "tags",
              "Tags",
              (reviewedServer.tags ?? []).join(", "),
              reviewedLocal.tags,
            ],
            [
              "description",
              "Description",
              reviewedServer.description ?? "",
              reviewedLocal.description,
            ],
            [
              "seoTitle",
              "SEO title",
              reviewedServer.seoTitle ?? "",
              reviewedLocal.seoTitle,
            ],
            [
              "seoDescription",
              "SEO description",
              reviewedServer.seoDescription ?? "",
              reviewedLocal.seoDescription,
            ],
            ["slug", "URL slug", reviewedServer.slug ?? "", reviewedLocal.slug],
          ] as const
        )
          .filter(([, , serverValue, localValue]) => serverValue.trim() !== localValue.trim())
          .map(([key, label, serverValue, localValue]) => ({
            key,
            label,
            serverValue,
            localValue,
          }))
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
                ? `${conflictDiffs.length} ${conflictDiffs.length === 1 ? "field differs" : "fields differ"} between the latest saved version and what you typed. Nothing is merged automatically.`
                : "The latest saved version and what you typed are now identical for every field shown here."}
            </DialogDescription>
          </DialogHeader>

          {conflictStaleToken && reviewedServer ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid="conflict-version-provenance"
            >
              You were editing the version saved at{" "}
              {formatDateTime(conflictStaleToken)}. Someone saved a newer one
              at {formatDateTime(reviewedServer.updatedAt)}.
            </p>
          ) : null}

          {conflictLocalSnapshot &&
          reviewedLocal &&
          !sameEditableValues(conflictLocalSnapshot, reviewedLocal) ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid="conflict-edited-since"
            >
              You have edited this draft since the conflict happened. The
              comparison below uses your current text, not what the failed
              save contained.
            </p>
          ) : null}

          {reviewOutOfDate ? (
            <Alert variant="destructive" data-testid="conflict-review-stale">
              <AlertDescription className="space-y-2">
                <p>
                  The draft changed after this comparison was created, so it no
                  longer shows what would be saved. Nothing has been written.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleOpenReview()}
                  data-testid="conflict-review-refresh"
                >
                  Refresh this comparison
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

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
              with exactly the values shown here, field by field, with nothing
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
                disabled={updateDraft.isPending || reviewOutOfDate}
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
