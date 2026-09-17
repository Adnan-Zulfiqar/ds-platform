"use client";

import { useEffect, useRef } from "react";
import { Loader2, Store } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { EditorTab } from "@/components/drafts/editor-header";
import type { ShopifyState } from "@/lib/editor-lifecycle";
import { editorTabForSection, sellerSectionLabel } from "@/lib/editor-section-labels";
import type {
  ShopifyPublishCheckItem,
  ShopifyPublishReadiness,
  ShopifyPublishResult,
} from "@/types/api";
import { DraftPostPublishPanel } from "@/components/drafts/draft-post-publish-panel";

type StoreOption = {
  id: string;
  name: string;
  status: string;
};

export type PublishSaveFailureReason =
  | "conflict"
  | "validation"
  | "auth"
  | "network"
  | "busy"
  | "missing_version"
  | "stale_completion"
  | "dirty_after_save";

type ReviewPublishPanelProps = {
  productId: string;
  stores: StoreOption[];
  storeId: string;
  onStoreChange: (storeId: string) => void;
  dirty: boolean;
  readiness: ShopifyPublishReadiness | undefined;
  readinessStatus: "idle" | "pending" | "error" | "success";
  readinessFetching: boolean;
  onRetryReadiness: () => void;
  saveFailureReason: PublishSaveFailureReason | null;
  onRetrySave: () => void;
  publishError: string | null;
  publishOk: string | null;
  publishPending: boolean;
  publishResult: ShopifyPublishResult | null;
  /** The editor's lifecycle view of Shopify — see `lib/editor-lifecycle.ts`. */
  shopify: ShopifyState;
  /** A conflict is unresolved; saving (and therefore publishing) is paused. */
  hasEditingConflict: boolean;
  onPublish: () => void;
  onOpenSection: (tab: EditorTab) => void;
  onContinueEditing: () => void;
};

function CheckItemList({
  items,
  tone,
  onOpenSection,
}: {
  items: ShopifyPublishCheckItem[];
  tone: "blocker" | "advice";
  onOpenSection: (tab: EditorTab) => void;
}) {
  return (
    <ul className="space-y-3" data-testid={`publish-${tone}-list`}>
      {items.map((item) => {
        // The API's section keys are its own vocabulary; the merchant sees
        // the editor section's name, and an unknown key shows nothing.
        const sectionLabel = sellerSectionLabel(item.section);
        const sectionTab = editorTabForSection(item.section);
        return (
        <li
          key={`${item.code}-${item.field ?? ""}-${item.message}`}
          className="rounded-md border border-border/80 bg-background p-3"
          data-testid={`publish-${tone}-item`}
          data-code={item.code}
        >
          <p className="text-sm font-medium text-foreground">{item.message}</p>
          {sectionLabel ? (
            <p className="mt-1 text-xs text-muted-foreground">
              In {sectionLabel}
            </p>
          ) : null}
          {item.action && sectionTab ? (
            <Button
              type="button"
              variant="link"
              className="mt-1 h-11 min-h-11 px-0 text-sm"
              onClick={() => onOpenSection(sectionTab)}
            >
              {item.action}
            </Button>
          ) : item.action === "Open Integrations" ? (
            <Button
              type="button"
              variant="link"
              className="mt-1 h-11 min-h-11 px-0 text-sm"
              asChild
            >
              <a href="/settings/integrations">{item.action}</a>
            </Button>
          ) : null}
        </li>
        );
      })}
    </ul>
  );
}

export function ReviewPublishPanel({
  productId,
  stores,
  storeId,
  onStoreChange,
  dirty,
  readiness,
  readinessStatus,
  readinessFetching,
  onRetryReadiness,
  saveFailureReason,
  onRetrySave,
  publishError,
  publishOk,
  publishPending,
  publishResult,
  shopify,
  hasEditingConflict,
  onPublish,
  onOpenSection,
  onContinueEditing,
}: ReviewPublishPanelProps) {
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const blockers = readiness?.blockers ?? [];
  const recommendations = readiness?.recommendations ?? [];
  const hasStore = Boolean(storeId);
  const checking =
    hasStore &&
    !dirty &&
    !saveFailureReason &&
    (readinessStatus === "pending" ||
      (readinessFetching && readinessStatus !== "error"));
  const checkUnavailable = hasStore && !dirty && readinessStatus === "error";
  // Presentation only: the editor's `handleSave` already refuses to save
  // over an unresolved conflict, so a click here could never publish.
  // Disabling the button says so before the click instead of after it.
  const canPublish =
    hasStore &&
    !hasEditingConflict &&
    !checking &&
    !checkUnavailable &&
    !saveFailureReason &&
    (dirty || (Boolean(readiness?.canPublish) && blockers.length === 0));
  const onShopify = shopify.hasSyncedListing && shopify.kind !== "publish-failed";
  const selectedStoreName = stores.find((store) => store.id === storeId)?.name ?? null;

  useEffect(() => {
    // The conflict banner takes focus on its own transition; moving focus
    // here as well would pull it away from the banner's actions.
    if (hasEditingConflict) return;
    if (!saveFailureReason && blockers.length === 0 && !publishError) return;
    summaryRef.current?.focus();
  }, [saveFailureReason, blockers.length, publishError, hasEditingConflict]);

  // "Validation passed" and "published" are different facts: the first is
  // the server's readiness check on the saved draft, the second is a
  // listing on Shopify. The summary only ever states the first; the
  // post-publish panel above states the second.
  let statusMessage = "Choose a store";
  if (hasEditingConflict) {
    statusMessage = "Resolve the editing conflict above first.";
  } else if (saveFailureReason) {
    statusMessage = "Your changes were not saved, so publishing was stopped.";
  } else if (!hasStore) {
    statusMessage = "Choose a store";
  } else if (checking) {
    statusMessage = "Checking this product…";
  } else if (checkUnavailable) {
    statusMessage = "We couldn’t check this product";
  } else if (blockers.length > 0) {
    statusMessage = `Fix before publishing · ${blockers.length} ${
      blockers.length === 1 ? "issue" : "issues"
    }`;
  } else if (dirty) {
    statusMessage =
      "We’ll save your latest changes, then check again when you publish.";
  } else if (shopify.kind === "changes-not-sent") {
    statusMessage = "Validation passed — your latest changes are ready to send to Shopify.";
  } else if (onShopify) {
    statusMessage = "Validation passed — publishing again sends the current DropPilot version.";
  } else {
    statusMessage = "Validation passed — no blocking issues.";
  }

  return (
    <section className="space-y-4" data-testid="publishing-panel">
      <h2 className="text-lg font-semibold">Review and publish</h2>
      <p className="text-sm text-muted-foreground">
        Send this draft to a connected Shopify store. Importing from the
        supplier is separate from publishing to your shop.
      </p>

      {onShopify ? (
        <DraftPostPublishPanel
          productId={productId}
          shopify={shopify}
          publishResult={publishResult}
          storeName={selectedStoreName}
          onContinueEditing={onContinueEditing}
        />
      ) : null}

      <div className="space-y-2">
        <Label htmlFor="publish-store">Shopify store</Label>
        <select
          id="publish-store"
          className="flex h-11 min-h-11 w-full rounded-md border border-input bg-background px-3 text-sm"
          value={storeId}
          onChange={(event) => onStoreChange(event.target.value)}
          data-testid="publish-store-select"
        >
          <option value="">Select a store…</option>
          {stores.map((store) => (
            <option key={store.id} value={store.id}>
              {store.name}
              {store.status !== "connected" ? ` (${store.status})` : ""}
            </option>
          ))}
        </select>
      </div>

      <div
        ref={summaryRef}
        tabIndex={-1}
        aria-live="polite"
        className="rounded-md border border-border/80 bg-muted/20 p-3 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="publish-status-summary"
      >
        <p className="text-sm font-medium text-foreground">{statusMessage}</p>
        {!hasStore && !saveFailureReason ? (
          <p className="mt-1 text-sm text-muted-foreground">
            Select where you want to publish this product.
          </p>
        ) : null}
        {checking ? (
          <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Checking this product…
          </p>
        ) : null}
        {checkUnavailable ? (
          <div className="mt-2 space-y-2">
            <p className="text-sm text-muted-foreground">
              Try again before publishing.
            </p>
            <Button
              type="button"
              variant="secondary"
              className="h-11 min-h-11"
              onClick={onRetryReadiness}
              data-testid="publish-readiness-retry"
            >
              Try again
            </Button>
          </div>
        ) : null}
        {!hasEditingConflict &&
        !checking &&
        !checkUnavailable &&
        hasStore &&
        !saveFailureReason &&
        blockers.length === 0 &&
        !dirty ? (
          <p className="mt-1 text-sm text-muted-foreground">
            We’ll check again when you publish.
          </p>
        ) : null}
        {hasEditingConflict ? (
          <p className="mt-1 text-sm text-muted-foreground" data-testid="publish-conflict-pointer">
            Saving is paused until you reload the latest version or review your changes, so nothing can be published yet.
          </p>
        ) : null}
        {dirty && hasStore && !saveFailureReason && !hasEditingConflict ? (
          <p className="mt-1 text-sm text-muted-foreground">
            Unsaved edits are saved first. Publishing stops if that save fails.
          </p>
        ) : null}
      </div>

      {saveFailureReason ? (
        <Alert variant="destructive" data-testid="publish-save-failure">
          <AlertDescription>
            <p className="font-medium">
              {saveFailureReason === "conflict"
                ? "This draft changed somewhere else. Review the latest version before publishing."
                : "We couldn’t save your changes. Your product was not published. Review the changes and try again."}
            </p>
            <p className="mt-1 text-sm">
              Your changes were not saved, so publishing was stopped.
            </p>
            {saveFailureReason !== "conflict" ? (
              <Button
                type="button"
                variant="secondary"
                className="mt-3 h-11 min-h-11"
                onClick={onRetrySave}
                data-testid="publish-save-retry"
              >
                Save and try again
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      {!dirty && !checking && !checkUnavailable && blockers.length > 0 ? (
        <div className="space-y-2" data-testid="publish-blockers">
          <h3 className="text-sm font-semibold">Fix before publishing</h3>
          <CheckItemList
            items={blockers}
            tone="blocker"
            onOpenSection={onOpenSection}
          />
        </div>
      ) : null}

      {!dirty && !checking && !checkUnavailable && recommendations.length > 0 ? (
        <div className="space-y-2" data-testid="publish-advice">
          <h3 className="text-sm font-semibold">Worth checking</h3>
          <p className="text-sm text-muted-foreground">
            These suggestions can improve your listing, but they do not stop
            publishing.
          </p>
          <CheckItemList
            items={recommendations}
            tone="advice"
            onOpenSection={onOpenSection}
          />
        </div>
      ) : null}

      {publishError ? (
        <Alert variant="destructive" data-testid="publish-error">
          <AlertDescription>{publishError}</AlertDescription>
        </Alert>
      ) : null}
      {publishOk ? (
        <Alert data-testid="publish-ok">
          <AlertDescription>{publishOk}</AlertDescription>
        </Alert>
      ) : null}

      <div className="space-y-2">
        <Button
          disabled={!canPublish || publishPending}
          onClick={onPublish}
          data-testid="publish-to-store"
          className="h-11 min-h-11"
          aria-describedby={
            !canPublish ? "publish-disabled-reason" : undefined
          }
        >
          {publishPending ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Store className="mr-1.5 h-4 w-4" aria-hidden />
          )}
          {publishPending ? "Publishing…" : onShopify ? "Update Shopify" : "Publish to Store"}
        </Button>
        {!canPublish ? (
          <p
            id="publish-disabled-reason"
            className="text-sm text-muted-foreground"
            data-testid="publish-disabled-reason"
          >
            {hasEditingConflict
              ? "Resolve the editing conflict above first."
              : saveFailureReason
              ? "Save your changes successfully before publishing."
              : !hasStore
                ? "Choose a store to continue."
                : checking
                  ? "Wait until the check finishes."
                  : checkUnavailable
                    ? "Run the check again before publishing."
                    :                     blockers.length > 0
                      ? "Fix the issues above before publishing."
                      : dirty
                        ? "Publishing will save first, then check again on the server."
                        : readiness?.canPublish
                          ? "Publishing is available."
                          : "Publishing is unavailable until the product can be checked."}
          </p>
        ) : null}
      </div>
    </section>
  );
}
