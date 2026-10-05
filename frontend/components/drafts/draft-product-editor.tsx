"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { Loader2 } from "lucide-react";

import { DraftInventoryPanel } from "@/components/drafts/draft-inventory-panel";
import { DraftMediaPanel } from "@/components/drafts/draft-media-panel";
import { DraftPricingPanel } from "@/components/drafts/draft-pricing-panel";
import { DraftSeoPanel } from "@/components/drafts/draft-seo-panel";
import { DraftShippingPanel } from "@/components/drafts/draft-shipping-panel";
import { DraftVariantsPanel } from "@/components/drafts/draft-variants-panel";
import { DraftPreviewPanel } from "@/components/drafts/draft-preview-panel";
import { EbayProductDetailsIfConnected } from "@/components/drafts/ebay-product-details";
import {
  ReviewPublishPanel,
  type PublishSaveFailureReason,
} from "@/components/drafts/review-publish-panel";
import {
  ProductEditorHeader,
  ProductEditorHeaderSkeleton,
} from "@/components/drafts/editor-header/product-editor-header";
import {
  isEditorTab,
  type EditorTab,
} from "@/components/drafts/editor-header/product-editor-tabs";
import { PublishChecklist } from "@/components/drafts/editor-header/publish-checklist";
import { formatSupplierSyncedAt, readinessFor } from "@/components/drafts/editor-header/readiness";
import { deriveEditorLifecycle } from "@/lib/editor-lifecycle";
import {
  DESCRIPTION_MAX_LENGTH,
  RichTextDescriptionEditor,
} from "@/components/drafts/rich-text-description-editor";
import type { SaveState } from "@/components/drafts/editor-header/save-state-indicator";
import { ProductVersionHistorySheet } from "@/components/products/product-version-history-sheet";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { formatDateTime, formatMoney } from "@/lib/utils";
import { ApiError } from "@/lib/api-client";
import {
  draftKeys,
  fetchDraft,
  publishDraft,
  useDraft,
  useDraftListings,
  useDraftSeoScore,
  useRefreshDraft,
  useUpdateDraft,
} from "@/services/drafts";
import {
  invalidatePublishReadiness,
  type PublishChannel,
  usePublishReadiness,
} from "@/services/publish-readiness";
import { useProductVersions } from "@/services/products";
import { useStores } from "@/services/stores";
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
const CHANNEL_NAME: Record<PublishChannel, string> = {
  shopify: "Shopify",
  ebay: "eBay",
  woocommerce: "WooCommerce",
};

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
  // Only to name the AI version live on a store; not fetched otherwise.
  const versionsQuery = useProductVersions(productId, {
    enabled: Boolean(
      listingsQuery.data?.some((row) => row.contentSource === "ai_version"),
    ),
  });
  // Store guidance copy and the Review & publish store list. Read once per
  // mount (React Query's staleTime), not polled: UX-L2D-07 removed a 15 s
  // interval that re-requested the list for every open editor to catch a
  // connect/disconnect made in another tab — a rare event a reload handles,
  // at the cost of four requests a minute per tab. Publication authority was
  // never here; the server's readiness check is what gates publishing.
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
  /**
   * Monotonic counters so an older save response cannot clear newer edits
   * or overwrite a newer save outcome. `dirtyEpoch` rises on every merchant
   * edit; `saveEpoch` rises when a save starts (and when the form is reset
   * from the server so in-flight responses are discarded).
   */
  const dirtyEpochRef = useRef(0);
  const saveEpochRef = useRef(0);
  const [publishStoreId, setPublishStoreId] = useState("");
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishPending, setPublishPending] = useState(false);
  const [publishOk, setPublishOk] = useState<string | null>(null);
  const [publishResult, setPublishResult] =
    useState<ShopifyPublishResult | null>(null);
  // When `publishResult` arrived. The lifecycle layer lets the response
  // stand in for the listings cache only while the cache is older than
  // this, so the header says "Added to Shopify" the instant the publish
  // returns and hands over to the server row as soon as it is refetched.
  const [publishResultAt, setPublishResultAt] = useState<number | null>(null);
  const [publishSaveFailure, setPublishSaveFailure] =
    useState<PublishSaveFailureReason | null>(null);
  const publishInFlightRef = useRef(false);
  // Review finding I-1: why Activate refused to run, or that the
  // product changed under in-progress edits.
  const [productActionNotice, setProductActionNotice] = useState<string | null>(null);
  const activationEpochRef = useRef(0);

  function markDirty() {
    dirtyEpochRef.current += 1;
    setDirty(true);
    setPublishSaveFailure(null);
  }
  const [inspectorOpen, setInspectorOpen] = useState(false);
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
  // Activate writes the Product row. Starting it over unsaved
  // edits would turn the merchant's own next save into a conflict (I-1).
  const productActionBlockedReason = isConflicted
    ? "Resolve the editing conflict first."
    : dirty
      ? "Save your changes first. This action updates the product, and your unsaved edits would then conflict with it."
      : null;
  // Focus target for the moment a conflict is detected (UX-L2D-05,
  // adapted from the reviewed historical branch). Only the `none ->
  // detected` transition moves focus: returning from the review dialog or
  // the reload confirmation also lands on "detected", and Radix already
  // restores focus to the control that opened them -- fighting that would
  // be focus theft on every phase change.
  const conflictBannerRef = useRef<HTMLDivElement | null>(null);
  const conflictReloadButtonRef = useRef<HTMLButtonElement | null>(null);
  const conflictReviewButtonRef = useRef<HTMLButtonElement | null>(null);
  const previousConflictPhaseRef = useRef(conflictPhase);
  // Both conflict dialogs are controlled and have no `DialogTrigger`, so
  // Radix's default close behaviour (focus the trigger) had nowhere to go
  // and focus fell to `<body>`. Return it to the banner button that opened
  // the dialog while the conflict is still open; once it is resolved the
  // banner is gone, so land on the field the merchant was editing or, on
  // another tab, on that tab.
  const returnFocusAfterConflictDialog = (
    event: Event,
    opener: RefObject<HTMLButtonElement | null>,
  ) => {
    event.preventDefault();
    const target =
      opener.current ??
      document.querySelector<HTMLElement>('[data-testid="draft-title-input"]') ??
      document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
    target?.focus();
  };
  useEffect(() => {
    const previous = previousConflictPhaseRef.current;
    previousConflictPhaseRef.current = conflictPhase;
    if (previous === "none" && conflictPhase === "detected") {
      conflictBannerRef.current?.focus();
    }
  }, [conflictPhase]);
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
    // Invalidate any in-flight save so a late response cannot clear a
    // freshly hydrated form or mark a discarded draft as saved.
    dirtyEpochRef.current += 1;
    saveEpochRef.current += 1;
    setDirty(false);
    setSaveState("idle");
  }

  /** Adopt a server version as the editor's new baseline: its values become
   * the form, its `updatedAt` becomes the active concurrency token.
   *
   * The *only* safe transitions, all of them explicit:
   *   1. the first successful load of a draft (nothing to lose yet);
   *   2. a confirmed "Reload latest version" (merchant chose to discard);
   *   3. an editor-initiated product-row action (Activate a
   *      version) succeeded, it could only start on a clean editor, and no
   *      edit has happened since it started — see `adoptAfterProductAction`
   *      (review finding I-1). Nothing unsaved exists to lose, and the
   *      returned product is the authoritative state *including* any change
   *      made elsewhere, so adopting it hides nothing;
   *   4. — reserved — any future transition must be added here deliberately,
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

  /** Review finding I-1. Activate writes the Product row and moves its
   * `updatedAt` (legacy Optimize did too; Phase 9 Stage 10 replaced it with
   * an AI Studio link, which writes nothing from here). Without this, the editor kept the old token and
   * its next save hit a 409 the merchant did not cause.
   *
   * Activate refuses to start while the editor is dirty or conflicted
   * (`productActionBlockedReason`). On success, if no edit happened since
   * the action started, the returned product becomes the baseline
   * (transition 3 above). If the merchant typed while it ran, nothing is
   * adopted silently: they are told the next save will ask them to review. */
  function adoptAfterProductAction(product: ProductDetail, editEpochAtStart: number) {
    if (dirtyEpochRef.current === editEpochAtStart) {
      hydrateFromServer(product);
      setProductActionNotice(null);
      return;
    }
    setProductActionNotice(
      "This product was updated while you were editing. Your next save will ask you to review both versions.",
    );
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

  // Follow ?tab= when it changes (a readiness link, Back/forward), adjusting
  // state during render rather than in an effect.
  const [followedTabParam, setFollowedTabParam] = useState(tabParam);
  if (followedTabParam !== tabParam) {
    setFollowedTabParam(tabParam);
    if (isEditorTab(tabParam)) setTab(tabParam);
  }

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

  type SaveResult =
    | { ok: true; updatedAt: string }
    | { ok: false; reason: PublishSaveFailureReason };

  async function handleSave(event?: FormEvent): Promise<SaveResult> {
    event?.preventDefault();
    // Guards against two hazards at once: a double-click or a keyboard
    // shortcut firing while a save is already in flight (no concurrent
    // duplicate requests), and autosave silently retrying over an
    // unresolved conflict -- the merchant must explicitly choose "Reload
    // latest version" or "Review my changes" first (see the conflict
    // banner below). `handleSaveMyVersionAnyway` is the one save path that
    // deliberately bypasses this guard, because it *is* the explicit,
    // reviewed choice this guard exists to require first.
    if (updateDraft.isPending || isConflicted) {
      return { ok: false, reason: isConflicted ? "conflict" : "busy" };
    }
    // The form only renders once `data` has loaded (see the early returns
    // below), and the `[data]` effect always sets this in the same tick --
    // reaching here without it would mean saving against no known version
    // at all, which the backend now rejects outright. Bail rather than
    // send a request guaranteed to 422.
    if (!savedUpdatedAt) {
      return { ok: false, reason: "missing_version" };
    }

    const dirtyEpochAtStart = dirtyEpochRef.current;
    const thisSave = ++saveEpochRef.current;
    setFormError(null);
    setSaveState("saving");

    try {
      const saved = await updateDraft.mutateAsync(
        buildSavePayload(captureEditableSnapshot(), savedUpdatedAt),
      );
      // Discard stale completions: a newer save may have started, or the
      // form may have been reset from the server while this request flew.
      if (thisSave !== saveEpochRef.current) {
        return { ok: false, reason: "stale_completion" };
      }
      // The server's response is authoritative: the next save's version
      // check is against what was *actually* persisted, not a value
      // computed client-side.
      setSavedUpdatedAt(saved.updatedAt);
      setProductActionNotice(null);
      invalidatePublishReadiness(queryClient, productId);
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
      // Only clear dirty when no edits landed after this request started.
      // Newer unsaved edits must not be authorised by this save result.
      if (dirtyEpochRef.current === dirtyEpochAtStart) {
        setDirty(false);
        setSaveState("saved");
        return { ok: true, updatedAt: saved.updatedAt };
      }
      setSaveState("idle");
      return { ok: false, reason: "dirty_after_save" };
    } catch (err) {
      if (thisSave !== saveEpochRef.current) {
        return { ok: false, reason: "stale_completion" };
      }
      if (err instanceof ApiError && err.status === 409) {
        // A newer save landed elsewhere since this editor last loaded.
        // Surfaced as its own state, not folded into `formError` -- the
        // recovery here is "reload or review", not "fix a field and
        // retry", and the two must not look the same to the merchant.
        await enterConflict(savedUpdatedAt);
        return { ok: false, reason: "conflict" };
      }
      setSaveState("error");
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setFormError("You need to sign in again before saving.");
        return { ok: false, reason: "auth" };
      }
      if (err instanceof ApiError && err.status !== null && err.status < 500) {
        setFormError(
          err.message ||
            "We couldn’t save your changes. Check the highlighted fields and try again.",
        );
        return { ok: false, reason: "validation" };
      }
      setFormError(
        "We couldn’t save your changes. Check your connection and try again.",
      );
      return { ok: false, reason: "network" };
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
      const latest = await fetchDraft(productId);
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
        const latest = await fetchDraft(productId);
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
    const dirtyEpochAtStart = dirtyEpochRef.current;
    const thisSave = ++saveEpochRef.current;
    setSaveState("saving");

    try {
      // Exactly the values the dialog rendered, against exactly the version
      // it named. Both come out of the same frozen object.
      const saved = await updateDraft.mutateAsync(
        buildSavePayload(reviewSnapshot.local, reviewSnapshot.server.updatedAt),
      );
      if (thisSave !== saveEpochRef.current) return;
      // The merchant's values won and are now the persisted truth. The
      // response is authoritative for the new baseline -- but note it is
      // only the *token* and dirty flag that move here, never the form
      // fields: those already hold exactly what was sent.
      if (dirtyEpochRef.current === dirtyEpochAtStart) {
        setDirty(false);
        setSaveState("saved");
      } else {
        setSaveState("idle");
      }
      setSavedUpdatedAt(saved.updatedAt);
      setProductActionNotice(null);
      hydratedFromRef.current = saved.id;
      clearConflict();
      void queryClient.invalidateQueries({
        queryKey: draftKeys.seoScore(productId),
      });
    } catch (err) {
      if (thisSave !== saveEpochRef.current) return;
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

  /** `replaceAiContent` is set only from the confirmed "Use my draft text
   * instead" dialog (review finding E-1). Every other publish keeps approved
   * AI text that is live on the store; the server enforces that too. */
  // EBAY-C3 / Track E7: readiness and publish go to the chosen store's channel.
  const publishPlatform = storesQuery.data?.items.find(
    (store) => store.id === publishStoreId,
  )?.platform;
  const publishChannel: PublishChannel =
    publishPlatform === "ebay" || publishPlatform === "woocommerce"
      ? publishPlatform
      : "shopify";

  async function handlePublish(options: { replaceAiContent?: boolean } = {}) {
    setPublishError(null);
    setPublishOk(null);
    setPublishResult(null);
    setPublishSaveFailure(null);
    if (!publishStoreId) {
      setPublishError("Select a connected store.");
      return;
    }
    if (publishInFlightRef.current || publishPending) {
      return;
    }
    publishInFlightRef.current = true;
    setPublishPending(true);
    try {
      let expectedUpdatedAt = savedUpdatedAt;
      if (dirty) {
        const saveResult = await handleSave();
        if (!saveResult.ok) {
          setPublishSaveFailure(saveResult.reason);
          if (saveResult.reason === "conflict") {
            setPublishError(
              "This draft changed somewhere else. Review the latest version before publishing.",
            );
          } else {
            setPublishError(
              "We couldn’t save your changes. Your product was not published. Review the changes and try again.",
            );
          }
          return;
        }
        expectedUpdatedAt = saveResult.updatedAt;
      }
      if (!expectedUpdatedAt) {
        setPublishSaveFailure("missing_version");
        setPublishError(
          "We couldn’t save your changes. Your product was not published. Review the changes and try again.",
        );
        return;
      }

      const result = await publishDraft(publishChannel, {
        productId,
        storeId: publishStoreId,
        expectedUpdatedAt,
        ...(options.replaceAiContent && publishChannel === "shopify"
          ? { replaceAiContent: true }
          : {}),
      });
      setPublishResult(result);
      setPublishResultAt(Date.now());
      const baseMessage = result.message || "Publish completed.";
      setPublishOk(
        result.contentSource === "ai_version"
          ? `${baseMessage} Shopify kept your approved AI title and description.`
          : options.replaceAiContent
            ? `${baseMessage} Shopify now shows your draft title and description.`
            : baseMessage,
      );
      void refetch();
      void queryClient.invalidateQueries({
        queryKey: draftKeys.listings(productId),
      });
      invalidatePublishReadiness(queryClient, productId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        if (err.code === "shopify_publish_busy") {
          setPublishError(
            "Publishing is already in progress. Please try again in a moment.",
          );
          return;
        }
        // Not every 409 is an edit conflict. This one means the approved AI
        // text the store shows can no longer be read; the only way forward
        // is the explicit "Use my draft text instead" choice (E-1). Opening
        // the conflict review here would blame an edit that never happened.
        const reason = err.details?.find((detail) => detail.type === "reason")?.message;
        if (reason === "published_ai_content_unavailable") {
          setPublishError(
            "The approved AI text live on Shopify can no longer be read. To publish, choose “Use my draft text instead…”.",
          );
          void queryClient.invalidateQueries({ queryKey: draftKeys.listings(productId) });
          return;
        }
        setPublishError(
          "This draft changed somewhere else. Review the latest version before publishing.",
        );
        await enterConflict(savedUpdatedAt);
        return;
      }
      if (err instanceof ApiError && err.status === null) {
        // No reply is not a failure: the publish may have reached Shopify
        // (the server holds the product lock while it calls Shopify). Re-read
        // what the store shows instead of claiming it did not happen.
        setPublishError(
          "We didn’t get a reply. The publish may still have completed — the listing status below is being re-checked.",
        );
        void queryClient.invalidateQueries({ queryKey: draftKeys.listings(productId) });
        void refetch();
        return;
      }
      if (err instanceof ApiError) {
        const blocked =
          err.details?.some((detail) => detail.type === "reason" && detail.message === "publish_blocked") ||
          err.code === "validation_error";
        setPublishError(
          blocked
            ? err.message || "Fix the issues below before publishing."
            : err.message || "Publish to Store failed.",
        );
        invalidatePublishReadiness(queryClient, productId);
        return;
      }
      setPublishError("Publish to Store failed. Try again in a moment.");
    } finally {
      publishInFlightRef.current = false;
      setPublishPending(false);
    }
  }

  const publishReadinessQuery = usePublishReadiness({
    productId,
    channel: publishChannel,
    storeId: publishStoreId || null,
    draftUpdatedAt: dirty ? null : savedUpdatedAt,
    // Every tab, once a store is chosen: the "Before you publish" sidebar
    // shows these server blockers too, not only Review & publish (DE-6b).
    enabled: Boolean(publishStoreId) && !dirty,
  });

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
  // Review & publish offers Shopify stores and, once listing setup has
  // created them, connected eBay marketplace stores (EBAY-C3).
  const publishStores =
    storesQuery.data?.items.filter(
      (store) =>
        store.platform === "shopify" ||
        ((store.platform === "ebay" || store.platform === "woocommerce") &&
          store.status === "connected"),
    ) ?? [];
  const syncedListing =
    listingsQuery.data?.find((row) => row.status === "synced") ??
    listingsQuery.data?.[0] ??
    null;
  // Review finding E-1: does the selected store show approved AI text?
  const selectedStoreListing =
    listingsQuery.data?.find((row) => row.storeId === publishStoreId) ?? null;
  const liveAiVersionId =
    selectedStoreListing?.contentSource === "ai_version"
      ? (selectedStoreListing.contentVersionId ?? null)
      : null;
  const liveAiContent = liveAiVersionId
    ? {
        versionNumber:
          versionsQuery.data?.items.find((version) => version.id === liveAiVersionId)
            ?.versionNumber ?? null,
      }
    : null;
  // One derivation for every surface that talks about state -- header,
  // save indicator, primary action, mobile bar, post-publish panel, Review
  // & publish. `savedUpdatedAt` is the version the server last confirmed,
  // which is the only "saved draft" timestamp that can be compared with a
  // listing's `lastSyncedAt`.
  const lifecycle = deriveEditorLifecycle({
    productStatus: data.status,
    dirty,
    saveState,
    conflict: isConflicted,
    publishPending,
    publishFailed: Boolean(publishError),
    publishResult,
    publishResultAt,
    listings: {
      data: listingsQuery.data,
      isPending: listingsQuery.isPending,
      isFetching: listingsQuery.isFetching,
      isError: listingsQuery.isError,
      dataUpdatedAt: listingsQuery.dataUpdatedAt,
    },
    draftUpdatedAt: savedUpdatedAt,
    issueCount: readiness.items.length,
  });
  // Review & publish speaks about the chosen store only (EBAY-C3 review):
  // an eBay store must not inherit "Update Shopify" from a Shopify listing,
  // and Shopify copy must not come from an eBay listing.
  const otherChannelStoreIds = new Set(
    (storesQuery.data?.items ?? [])
      .filter((s) => s.platform === "ebay" || s.platform === "woocommerce")
      .map((s) => s.id),
  );
  const panelListings =
    publishChannel !== "shopify"
      ? listingsQuery.data?.filter((row) => row.storeId === publishStoreId)
      : listingsQuery.data?.filter((row) => !otherChannelStoreIds.has(row.storeId));
  const panelShopifyState = deriveEditorLifecycle({
    productStatus: data.status,
    dirty,
    saveState,
    conflict: isConflicted,
    publishPending,
    publishFailed: Boolean(publishError),
    publishResult,
    publishResultAt,
    listings: {
      data: panelListings,
      isPending: listingsQuery.isPending,
      isFetching: listingsQuery.isFetching,
      isError: listingsQuery.isError,
      dataUpdatedAt: listingsQuery.dataUpdatedAt,
    },
    draftUpdatedAt: savedUpdatedAt,
    issueCount: readiness.items.length,
  }).shopify;

  const focusConflictBanner = () => {
    conflictBannerRef.current?.scrollIntoView({ block: "center" });
    conflictBannerRef.current?.focus();
  };

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
        productId={productId}
        product={{ ...data, title: title || data.title }}
        activeTab={tab}
        onTabChange={selectTab}
        lifecycle={lifecycle}
        dirty={dirty}
        saving={updateDraft.isPending || saveState === "saving"}
        listingsRetrying={listingsQuery.isFetching && listingsQuery.data === undefined}
        shopifyStores={shopifyStores}
        storesPending={storesQuery.isPending}
        storesError={storesQuery.isError}
        seoScore={seoScoreQuery.data}
        refreshing={refreshDraft.isPending}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => setInspectorOpen((open) => !open)}
        onPreview={() => setPreviewOpen(true)}
        onSave={() => void handleSave()}
        onPublish={() => selectTab("publishing")}
        onResolveConflict={focusConflictBanner}
        onRetryListings={() => void listingsQuery.refetch()}
        onRefresh={() => void refreshDraft.mutateAsync()}
        aiStudioHref={`/ai-studio/products/${productId}`}
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
        activationBlockedReason={productActionBlockedReason}
        onActivationStart={() => {
          activationEpochRef.current = dirtyEpochRef.current;
        }}
        onActivated={(product) => adoptAfterProductAction(product, activationEpochRef.current)}
      />

      {productActionNotice ? (
        <Alert data-testid="editor-product-action-notice">
          <AlertDescription>{productActionNotice}</AlertDescription>
        </Alert>
      ) : null}

      {isConflicted ? (
        <Alert
          variant="destructive"
          role="alert"
          ref={conflictBannerRef}
          tabIndex={-1}
          className="scroll-mt-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                className="min-h-11"
                ref={conflictReloadButtonRef}
                onClick={handleRequestReload}
                data-testid="conflict-reload-latest"
              >
                Reload latest version
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-11"
                ref={conflictReviewButtonRef}
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
        <DialogContent
          data-testid="conflict-reload-confirm-dialog"
          onCloseAutoFocus={(event) =>
            returnFocusAfterConflictDialog(event, conflictReloadButtonRef)
          }
        >
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
          onCloseAutoFocus={(event) =>
            returnFocusAfterConflictDialog(event, conflictReviewButtonRef)
          }
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
                Product details
              </h2>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2 md:col-span-2">
                  <Label htmlFor="draft-title">Title</Label>
                  <Input
                    id="draft-title"
                    value={title}
                    onChange={(event) => {
                      setTitle(event.target.value);
                      markDirty();
                    }}
                    data-testid="draft-title-input"
                  />
                  {title.trim().length > 0 && title.trim().length < 8 ? (
                    <p className="text-sm text-amber-800 dark:text-amber-300" role="status">
                      Add a clearer product title so shoppers can recognise
                      this listing.
                    </p>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="draft-brand">Brand</Label>
                  <Input
                    id="draft-brand"
                    value={brand}
                    onChange={(event) => {
                      setBrand(event.target.value);
                      markDirty();
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
                      markDirty();
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
                      markDirty();
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
                      markDirty();
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
                      {formatSupplierSyncedAt(data.lastSyncedAt) ?? "—"}
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
                Write your listing description. Supplier refresh will not
                overwrite this field once it differs from the supplier snapshot.
              </p>
              <div className="space-y-2">
                <Label htmlFor="draft-description-editor">Merchant description</Label>
                {/* M2B: replaced a raw-HTML textarea. The editor is WYSIWYG,
                    but the stored format is unchanged -- sanitized HTML in
                    the same `description` column -- so existing drafts and
                    the Shopify publish path need no migration. The separate
                    "HTML preview" pane that used to sit here is gone: the
                    editor now *is* the preview, and a second rendering of
                    the same string was only ever useful when the input
                    showed source. */}
                <RichTextDescriptionEditor
                  value={description}
                  disabled={isConflicted}
                  maxLength={DESCRIPTION_MAX_LENGTH}
                  onChange={(html) => {
                    setDescription(html);
                    markDirty();
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
                markDirty();
              }}
            />
          ) : null}

          {tab === "publishing" ? (
            <ReviewPublishPanel
              productId={productId}
              stores={publishStores.map((store) => ({
                id: store.id,
                name: store.name,
                status: store.status,
              }))}
              storeId={publishStoreId}
              channelName={CHANNEL_NAME[publishChannel]}
              dirty={dirty}
              onStoreChange={(next) => {
                setPublishStoreId(next);
                setPublishSaveFailure(null);
                setPublishError(null);
                invalidatePublishReadiness(queryClient, productId);
              }}
              readiness={publishReadinessQuery.data}
              readinessStatus={
                !publishStoreId || dirty
                  ? "idle"
                  : publishReadinessQuery.isError
                    ? "error"
                    : publishReadinessQuery.isPending
                      ? "pending"
                      : publishReadinessQuery.isSuccess
                        ? "success"
                        : "idle"
              }
              readinessFetching={publishReadinessQuery.isFetching}
              onRetryReadiness={() => {
                void publishReadinessQuery.refetch();
              }}
              saveFailureReason={publishSaveFailure}
              onRetrySave={() => {
                void (async () => {
                  setPublishSaveFailure(null);
                  setPublishError(null);
                  const result = await handleSave();
                  if (!result.ok) {
                    setPublishSaveFailure(result.reason);
                  }
                })();
              }}
              publishError={publishError}
              publishOk={publishOk}
              publishPending={publishPending}
              publishResult={publishResult}
              shopify={panelShopifyState}
              hasEditingConflict={isConflicted}
              onPublish={() => void handlePublish()}
              liveAiContent={liveAiContent}
              onReplaceAiContent={() => void handlePublish({ replaceAiContent: true })}
              onOpenSection={(next) => selectTab(next)}
              onReloadDraft={() => void enterConflict(savedUpdatedAt)}
              onContinueEditing={() => selectTab("overview")}
            />
          ) : null}

          {tab === "publishing" ? <EbayProductDetailsIfConnected productId={productId} /> : null}

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

        </div>

        <PublishChecklist
          readiness={readiness}
          seoScore={seoScoreQuery.data}
          serverReadiness={publishStoreId && !dirty ? (publishReadinessQuery.data ?? null) : null}
          storeChosen={Boolean(publishStoreId)}
          listing={syncedListing}
          shopifyStores={shopifyStores}
          storesPending={storesQuery.isPending}
          storesError={storesQuery.isError}
          open={inspectorOpen}
          onClose={() => setInspectorOpen(false)}
          onOpenTab={selectTab}
          variant="aside"
        />
        <PublishChecklist
          readiness={readiness}
          seoScore={seoScoreQuery.data}
          serverReadiness={publishStoreId && !dirty ? (publishReadinessQuery.data ?? null) : null}
          storeChosen={Boolean(publishStoreId)}
          listing={syncedListing}
          shopifyStores={shopifyStores}
          storesPending={storesQuery.isPending}
          storesError={storesQuery.isError}
          open={inspectorOpen}
          onClose={() => setInspectorOpen(false)}
          onOpenTab={(next) => {
            setInspectorOpen(false);
            selectTab(next);
          }}
          variant="sheet"
        />
      </div>

    </div>
  );
}
