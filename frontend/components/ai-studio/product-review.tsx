"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApproveDialog } from "@/components/ai-studio/approve-dialog";
import { CandidateComparison } from "@/components/ai-studio/candidate-comparison";
import { ImageEvidence } from "@/components/ai-studio/image-evidence";
import { LegacyVersionPanel } from "@/components/ai-studio/legacy-version-panel";
import { LiveOnShopify } from "@/components/ai-studio/live-on-shopify";
import { PublishResultPanel } from "@/components/ai-studio/publish-result";
import { QualityPanel } from "@/components/ai-studio/quality-panel";
import { ReadinessPanel } from "@/components/ai-studio/readiness-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  isStaleCandidateError,
  reasonOf,
  requestIdOf,
  studioErrorMessage,
} from "@/lib/ai-studio/errors";
import {
  approvalTokenOf,
  hasStalePreviewBlocker,
  isTestPreview,
  newerToken,
  publishTokenOf,
} from "@/lib/ai-studio/tokens";
import {
  useApprovePipelineCandidate,
  usePipelineCandidate,
  usePipelinePreview,
  useProduct,
  usePublishPipelineCandidate,
} from "@/services/products";
import { useStores } from "@/services/stores";
import type { AITone } from "@/types/api";

const TONES: { value: AITone; label: string }[] = [
  { value: "professional", label: "Professional" },
  { value: "persuasive", label: "Persuasive" },
  { value: "luxury", label: "Luxury" },
  { value: "technical", label: "Technical" },
  { value: "friendly", label: "Friendly" },
];

function isUnansweredRequest(error: unknown): boolean {
  return error instanceof ApiError && (error.code === "timeout" || error.code === "network_error");
}

/**
 * One product's AI Studio review: preview → compare → approve → publish
 * (PHASE_9_STAGE_10_PLAN.md §6, §11–§15).
 *
 * Token state lives here and nowhere else — not in Zustand, not in the draft
 * form. The server stays authoritative: every enabled button follows a field
 * the API returned (`candidateActive`, `publishable`, `pipelineBlockers`),
 * never a rule re-implemented in React.
 */
export function ProductReview({ productId }: { productId: string }) {
  const router = useRouter();
  const candidateId = useSearchParams().get("candidate");

  const product = useProduct(productId);
  const storesQuery = useStores({ size: 50 });
  const shopifyStores = storesQuery.data?.items.filter((store) => store.platform === "shopify") ?? [];

  const [tone, setTone] = useState<AITone>("professional");
  const [chosenStoreId, setChosenStoreId] = useState<string | null>(null);
  // One connected store is the obvious target; with several the merchant
  // chooses, so readiness is never composed for a store they did not mean.
  const storeId = chosenStoreId ?? (shopifyStores.length === 1 ? (shopifyStores[0]?.id ?? null) : null);

  const preview = usePipelinePreview(productId);
  const candidate = usePipelineCandidate(productId, candidateId, storeId);
  const approve = useApprovePipelineCandidate(productId);
  const publish = usePublishPipelineCandidate(productId);

  // The approve response's `updatedAt` (T1) for the candidate it approved.
  const [approvedToken, setApprovedToken] = useState<{ versionId: string; token: string } | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);

  const candidateHeadingRef = useRef<HTMLHeadingElement>(null);
  const approvedBannerRef = useRef<HTMLDivElement>(null);
  const focusOnCandidate = useRef<string | null>(null);
  const focusOnApproved = useRef(false);

  const data = candidate.data;
  const approvedHere = approvedToken !== null && approvedToken.versionId === candidateId;
  const approved = Boolean(data?.candidateActive) || approvedHere;
  const approvalToken = data ? approvalTokenOf(data) : null;
  const publishToken = newerToken(
    approvedHere ? approvedToken.token : null,
    data ? publishTokenOf(data) : null,
  );
  const stale = (data !== undefined && hasStalePreviewBlocker(data)) || isStaleCandidateError(approve.error);
  const testPreview = data !== undefined && isTestPreview(data);
  const legacy =
    candidate.isError &&
    candidate.error instanceof ApiError &&
    candidate.error.code === "validation_error" &&
    reasonOf(candidate.error) === "not_a_pipeline_candidate";
  const candidateGone =
    candidate.isError && candidate.error instanceof ApiError && candidate.error.code === "not_found";

  const canApprove =
    data !== undefined && !data.candidateActive && !approvedHere && !stale && approvalToken !== null;
  const canPublish =
    data !== undefined &&
    !candidate.isFetching &&
    data.candidateActive &&
    data.publishable &&
    storeId !== null &&
    publishToken !== null &&
    !testPreview &&
    !publish.isPending;

  useEffect(() => {
    if (data && focusOnCandidate.current === data.candidateVersionId) {
      focusOnCandidate.current = null;
      candidateHeadingRef.current?.focus();
    }
  }, [data]);

  useEffect(() => {
    if (approvedHere && focusOnApproved.current) {
      focusOnApproved.current = false;
      approvedBannerRef.current?.focus();
    }
  }, [approvedHere]);

  function generate() {
    approve.reset();
    publish.reset();
    setApprovedToken(null);
    preview.mutate(
      { tone, ...(storeId ? { storeId } : {}) },
      {
        onSuccess: (created) => {
          focusOnCandidate.current = created.candidateVersionId;
          // Pin the exact candidate so refresh and Back GET it; a reload must
          // never POST another preview.
          router.replace(`/ai-studio/products/${productId}?candidate=${created.candidateVersionId}`);
        },
      },
    );
  }

  function confirmApprove() {
    if (!data || approvalToken === null) return;
    const versionId = data.candidateVersionId;
    approve.mutate(
      { versionId, expectedUpdatedAt: approvalToken },
      {
        onSuccess: (detail) => {
          setApprovedToken({ versionId, token: detail.updatedAt });
          setApproveOpen(false);
          focusOnApproved.current = true;
        },
        onError: () => setApproveOpen(false),
      },
    );
  }

  function runPublish() {
    if (!canPublish || !data || storeId === null || publishToken === null) return;
    publish.mutate({ versionId: data.candidateVersionId, storeId, expectedUpdatedAt: publishToken });
  }

  function chooseStore(next: string | null) {
    publish.reset();
    setChosenStoreId(next);
  }

  if (product.isPending) {
    return <Skeleton className="h-96 w-full" data-testid="ai-studio-review-loading" />;
  }
  if (product.isError) {
    const notFound = product.error instanceof ApiError && product.error.code === "not_found";
    return (
      <div data-testid={notFound ? "ai-studio-product-not-found" : "ai-studio-product-error"}>
        <ErrorState
          title={notFound ? "Product not found" : "Could not load this product"}
          description={
            notFound
              ? "This product may have been removed, or the link may be incorrect."
              : studioErrorMessage(product.error)
          }
          requestId={requestIdOf(product.error)}
          onRetry={notFound ? undefined : () => void product.refetch()}
        />
        <div className="mt-4 flex justify-center">
          <Button asChild variant="outline">
            <Link href="/ai-studio">Back to AI Studio</Link>
          </Button>
        </div>
      </div>
    );
  }

  const detail = product.data;

  return (
    <div className="space-y-6 pb-24 lg:pb-0" data-testid="ai-studio-review">
      <PageHeader
        title={detail.title || "Untitled product"}
        description="Review an AI proposal for this product. Nothing is approved or published without your confirmation."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link href="/ai-studio">Back to AI Studio</Link>
          </Button>
        }
      />

      <section className="grid gap-4 rounded-lg border p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end" aria-label="Preview options">
        <div className="space-y-1">
          <Label htmlFor="ai-studio-tone">Tone</Label>
          <select
            id="ai-studio-tone"
            className="h-10 w-full rounded-md border bg-background px-3 text-sm"
            value={tone}
            onChange={(event) => setTone(event.target.value as AITone)}
          >
            {TONES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="ai-studio-store">Store</Label>
          <select
            id="ai-studio-store"
            className="h-10 w-full rounded-md border bg-background px-3 text-sm"
            value={storeId ?? ""}
            onChange={(event) => chooseStore(event.target.value || null)}
            disabled={storesQuery.isPending}
          >
            <option value="">{shopifyStores.length === 0 ? "No Shopify store connected" : "Choose a store"}</option>
            {shopifyStores.map((store) => (
              <option key={store.id} value={store.id}>
                {store.name}
              </option>
            ))}
          </select>
        </div>
        <Button
          type="button"
          onClick={generate}
          disabled={preview.isPending}
          data-testid="ai-studio-generate"
        >
          {preview.isPending ? "Generating…" : data ? "Generate new preview" : "Generate preview"}
        </Button>
      </section>

      <div aria-live="polite" className="sr-only" data-testid="ai-studio-live-region">
        {preview.isPending ? "Generating preview" : ""}
      </div>

      {preview.isError ? (
        <Alert variant="destructive" data-testid="ai-studio-preview-error">
          <AlertDescription>
            {isUnansweredRequest(preview.error)
              ? "The preview is taking longer than expected. It may still be created — check version history before generating again."
              : studioErrorMessage(preview.error)}
            {requestIdOf(preview.error) ? (
              <span className="block text-xs">Reference: {requestIdOf(preview.error)}</span>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      <LiveOnShopify
        productId={productId}
        storeId={storeId}
        candidateVersionId={data?.candidateVersionId ?? null}
        candidateVersionNumber={data?.candidateVersionNumber ?? null}
      />

      {candidateId === null && !preview.isPending ? (
        <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground" data-testid="ai-studio-no-candidate">
          Generate a preview to see an AI proposal next to your current draft. Generating never
          approves or publishes anything.
        </div>
      ) : null}

      {candidateId !== null && candidate.isPending ? (
        <Skeleton className="h-64 w-full" data-testid="ai-studio-candidate-loading" />
      ) : null}

      {legacy && candidateId ? <LegacyVersionPanel productId={productId} versionId={candidateId} /> : null}

      {candidateGone ? (
        <ErrorState
          title="Version not found"
          description="This AI version no longer exists. Generate a new preview."
          requestId={requestIdOf(candidate.error)}
        />
      ) : null}

      {candidate.isError && !legacy && !candidateGone ? (
        <Alert variant="destructive" data-testid="ai-studio-candidate-error">
          <AlertDescription>
            {studioErrorMessage(candidate.error)}
            {requestIdOf(candidate.error) ? (
              <span className="block text-xs">Reference: {requestIdOf(candidate.error)}</span>
            ) : null}
            <Button type="button" variant="link" className="px-0" onClick={() => void candidate.refetch()}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {data ? (
        <>
          {testPreview ? (
            <Alert data-testid="ai-studio-test-preview">
              <AlertTitle>Test AI preview</AlertTitle>
              <AlertDescription>
                This text came from the test provider. It cannot be published to Shopify.
              </AlertDescription>
            </Alert>
          ) : null}

          {stale ? (
            <Alert variant="destructive" data-testid="ai-studio-stale">
              <AlertTitle>This product changed after the preview was generated</AlertTitle>
              <AlertDescription className="space-y-2">
                <p>This candidate can no longer be approved. Generate a fresh preview to review the current product.</p>
                <Button type="button" size="sm" onClick={generate} disabled={preview.isPending} data-testid="ai-studio-regenerate">
                  Generate fresh preview
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          {approvedHere ? (
            <div
              ref={approvedBannerRef}
              tabIndex={-1}
              className="rounded-md border border-success/40 bg-success/10 p-3 text-sm focus:outline-none"
              data-testid="ai-studio-approved"
            >
              Approved. Your draft and your Shopify listing are unchanged.
            </div>
          ) : null}

          {approve.isError && !isStaleCandidateError(approve.error) ? (
            <Alert variant="destructive" data-testid="ai-studio-approve-error">
              <AlertDescription>
                {isUnansweredRequest(approve.error)
                  ? "We did not get a reply. Your approval may have been saved — approving again is safe and will confirm it."
                  : studioErrorMessage(approve.error)}
                {requestIdOf(approve.error) ? (
                  <span className="block text-xs">Reference: {requestIdOf(approve.error)}</span>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}

          <CandidateComparison
            ref={candidateHeadingRef}
            preview={data}
            approved={approved}
            activeVersionNumber={detail.aiVersion}
          />

          <div className="grid gap-4 lg:grid-cols-2">
            <QualityPanel preview={data} />
            <ReadinessPanel preview={data} storeSelected={storeId !== null} />
          </div>

          <ImageEvidence images={data.imageAnalysis.images} />

          {publish.isError ? (
            <Alert variant="destructive" data-testid="ai-studio-publish-error">
              <AlertDescription>
                {isUnansweredRequest(publish.error)
                  ? "We did not get a reply from the server. The publish may still have completed — the Live on Shopify line above is being re-checked."
                  : studioErrorMessage(publish.error)}
                {requestIdOf(publish.error) ? (
                  <span className="block text-xs">Reference: {requestIdOf(publish.error)}</span>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}

          {publish.isSuccess ? <PublishResultPanel result={publish.data} /> : null}

          {approved && candidate.isFetching ? (
            <p className="text-sm text-muted-foreground" data-testid="ai-studio-refreshing">
              Checking Shopify readiness for the approved version…
            </p>
          ) : null}

          <div
            className="fixed inset-x-0 bottom-0 z-20 flex flex-wrap justify-end gap-2 border-t bg-background p-3 lg:static lg:border-0 lg:bg-transparent lg:p-0"
            data-testid="ai-studio-actions"
          >
            {!data.candidateActive && !approvedHere ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => setApproveOpen(true)}
                disabled={!canApprove || approve.isPending}
                data-testid="ai-studio-approve"
              >
                {approve.isPending ? "Approving…" : "Approve this version"}
              </Button>
            ) : null}
            <Button
              type="button"
              onClick={runPublish}
              disabled={!canPublish}
              data-testid="ai-studio-publish"
            >
              {publish.isPending ? "Publishing…" : "Publish approved version"}
            </Button>
          </div>

          <ApproveDialog
            open={approveOpen}
            onOpenChange={setApproveOpen}
            onConfirm={confirmApprove}
            pending={approve.isPending}
          />
        </>
      ) : null}
    </div>
  );
}
