"use client";

import Link from "next/link";
import { useState } from "react";
import { History, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { useActivateProductVersion, useProductVersions } from "@/services/products";
import type { ProductDetail, ProductVersion } from "@/types/api";

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

type ActivationOptions = {
  /** Why Activate cannot run right now (e.g. unsaved editor changes). */
  activationBlockedReason?: string | null;
  /** Called as an activation request starts, so an open editor can note
   * whether the merchant edits while it runs. */
  onActivationStart?: () => void;
  /** Receives the authoritative product after a successful activation, so
   * an open editor can adopt its new `updatedAt` (review finding I-1). */
  onActivated?: (product: ProductDetail) => void;
};

function VersionRow({
  version,
  productId,
  activationBlockedReason = null,
  onActivationStart,
  onActivated,
}: {
  version: ProductVersion;
  productId: string;
} & ActivationOptions) {
  // Hook-level callback, not `mutate(..., { onSuccess })`: closing the sheet
  // unmounts this row, and a per-call callback would then never run.
  const activate = useActivateProductVersion(productId, { onActivated });
  // Review finding I-2: the API refuses plain Activate for pipeline
  // candidates (422 pipeline_candidate_requires_approval), so the control
  // is not offered. Approval is a separate, reviewed flow.
  const pipelineCandidate = version.isPipelineCandidate === true;

  return (
    <div data-testid="product-version-row" className="space-y-1 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">Version {version.versionNumber}</span>
          <Badge variant={version.source === "original" ? "secondary" : "default"}>
            {version.source === "original"
              ? "Original"
              : pipelineCandidate
                ? "AI candidate"
                : "AI generated"}
          </Badge>
          {version.active ? <Badge variant="outline">Active</Badge> : null}
        </div>
        {!version.active && !pipelineCandidate ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              onActivationStart?.();
              activate.mutate(version.id);
            }}
            disabled={activate.isPending || Boolean(activationBlockedReason)}
            data-testid="version-activate"
          >
            {activate.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              "Activate"
            )}
          </Button>
        ) : null}
      </div>
      {/* AI-generated text, rendered as plain text — never dangerouslySetInnerHTML. */}
      {version.title ? <p className="line-clamp-2 text-sm">{version.title}</p> : null}
      {pipelineCandidate && !version.active ? (
        <p className="text-xs text-muted-foreground" data-testid="version-pipeline-candidate-note">
          AI candidate awaiting review. It is approved in AI Studio, not activated from here.{" "}
          <Link
            className="underline"
            href={`/ai-studio/products/${productId}?candidate=${version.id}`}
            data-testid="version-review-in-studio"
          >
            Review in AI Studio
          </Link>
        </p>
      ) : null}
      {!version.active && !pipelineCandidate && activationBlockedReason ? (
        <p className="text-xs text-muted-foreground" data-testid="version-activate-blocked">
          {activationBlockedReason}
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">{formatDate(version.createdAt)}</p>
      {activate.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {activate.error instanceof Error
              ? activate.error.message
              : "Could not activate that version."}
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

function VersionHistoryList({
  productId,
  activationBlockedReason,
  onActivationStart,
  onActivated,
}: { productId: string } & ActivationOptions) {
  const { data, isPending, isError, error, refetch } = useProductVersions(productId);

  if (isPending) {
    return (
      <div className="space-y-2" data-testid="version-history-loading">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {error instanceof Error ? error.message : "Could not load version history."}
        </AlertDescription>
        <Button size="sm" variant="outline" className="mt-2" onClick={() => void refetch()}>
          Retry
        </Button>
      </Alert>
    );
  }

  if (data.items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No AI versions yet. Use AI Studio to generate and review the first one.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {data.items.map((version) => (
        <VersionRow
          key={version.id}
          version={version}
          productId={productId}
          activationBlockedReason={activationBlockedReason}
          onActivationStart={onActivationStart}
          onActivated={onActivated}
        />
      ))}
    </div>
  );
}

/**
 * A side panel showing a product's optimisation history.
 *
 * Foundation only, per Phase 9 stage 3's scope — a read-only list plus an
 * "Activate" action per version, not an editor. Reuses `Sheet`, `Badge`, and
 * `Alert` from the existing design system rather than introducing new
 * primitives.
 */
export function ProductVersionHistorySheet({
  productId,
  productTitle,
  open: openProp,
  onOpenChange,
  hideTrigger = false,
  compact = false,
  activationBlockedReason = null,
  onActivationStart,
  onActivated,
}: {
  productId: string;
  productTitle: string;
  /** Controlled open state for the editor More menu. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** When true, no permanent History button is rendered. */
  hideTrigger?: boolean;
  /** Icon-only trigger, named "History" for assistive technology (catalogue rows). */
  compact?: boolean;
} & ActivationOptions) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = openProp ?? uncontrolledOpen;
  const setOpen = onOpenChange ?? setUncontrolledOpen;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      {hideTrigger ? null : compact ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <SheetTrigger asChild>
              <Button size="icon" variant="ghost" className="h-9 w-9" aria-label="History">
                <History className="h-4 w-4" aria-hidden="true" />
              </Button>
            </SheetTrigger>
          </TooltipTrigger>
          <TooltipContent>Version history</TooltipContent>
        </Tooltip>
      ) : (
        <SheetTrigger asChild>
          <Button size="sm" variant="ghost">
            <History className="mr-2 h-4 w-4" aria-hidden="true" />
            History
          </Button>
        </SheetTrigger>
      )}

      <SheetContent side="right" className="w-full max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Version history</SheetTitle>
          <SheetDescription>{productTitle}</SheetDescription>
        </SheetHeader>

        <div className="mt-6">
          {open ? (
            <VersionHistoryList
              productId={productId}
              activationBlockedReason={activationBlockedReason}
              onActivationStart={onActivationStart}
              onActivated={onActivated}
            />
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
