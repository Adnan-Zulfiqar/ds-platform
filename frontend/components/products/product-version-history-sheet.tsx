"use client";

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
import { Skeleton } from "@/components/ui/skeleton";
import { useActivateProductVersion, useProductVersions } from "@/services/products";
import type { ProductVersion } from "@/types/api";

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function VersionRow({
  version,
  productId,
}: {
  version: ProductVersion;
  productId: string;
}) {
  const activate = useActivateProductVersion(productId);

  return (
    <div data-testid="product-version-row" className="space-y-1 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">Version {version.versionNumber}</span>
          <Badge variant={version.source === "original" ? "secondary" : "default"}>
            {version.source === "original" ? "Original" : "AI generated"}
          </Badge>
          {version.active ? <Badge variant="outline">Active</Badge> : null}
        </div>
        {!version.active ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => activate.mutate(version.id)}
            disabled={activate.isPending}
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

function VersionHistoryList({ productId }: { productId: string }) {
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
        Not optimized yet. Use &ldquo;Optimize with AI&rdquo; to create the first version.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {data.items.map((version) => (
        <VersionRow key={version.id} version={version} productId={productId} />
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
}: {
  productId: string;
  productTitle: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button size="sm" variant="ghost">
          <History className="mr-2 h-4 w-4" aria-hidden="true" />
          History
        </Button>
      </SheetTrigger>

      <SheetContent side="right" className="w-full max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Version history</SheetTitle>
          <SheetDescription>{productTitle}</SheetDescription>
        </SheetHeader>

        <div className="mt-6">{open ? <VersionHistoryList productId={productId} /> : null}</div>
      </SheetContent>
    </Sheet>
  );
}
