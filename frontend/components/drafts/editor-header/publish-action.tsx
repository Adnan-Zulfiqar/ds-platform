"use client";

import Link from "next/link";
import { AlertTriangle, ExternalLink, Loader2, Store } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { NextAction } from "@/lib/editor-lifecycle";
import { EXTERNAL_LINK_REL } from "@/lib/external-link";
import { cn } from "@/lib/utils";

interface PublishActionProps {
  action: NextAction;
  productId: string;
  /** Trusted admin URL, when the product is on Shopify; rendered as a secondary link. */
  adminUrl?: string | null;
  /** Open Review & publish. Header controls never publish. */
  onPublish: () => void;
  /** Move focus to the conflict banner. */
  onResolveConflict?: () => void;
  className?: string;
  size?: "default" | "sm";
}

/**
 * The header's one primary control, labelled by the lifecycle layer.
 *
 * It navigates — to Review & publish, to the product page, or to the
 * conflict banner — and never publishes; only the panel's own button does,
 * so a stray click in the header cannot start a channel request. Only an
 * in-flight publish disables it: advisory checklist items change the label
 * to "Review N items" and leave it clickable, because the server's channel
 * checks on Review & publish remain the authority.
 */
export function PublishAction({
  action,
  productId,
  adminUrl,
  onPublish,
  onResolveConflict,
  className,
  size = "default",
}: PublishActionProps) {
  const shared = {
    "data-testid": "publish-action",
    "data-publish-intent": "navigate",
    "data-action-kind": action.kind,
  } as const;

  if (action.kind === "view-product") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button size={size} variant="outline" asChild className={className} {...shared}>
          <Link href={`/products/${productId}`}>
            <Store className="mr-1.5 h-4 w-4" aria-hidden="true" />
            {action.label}
          </Link>
        </Button>
        {adminUrl ? (
          <Button size={size} variant="ghost" asChild data-testid="manage-in-shopify">
            <a href={adminUrl} target="_blank" rel={EXTERNAL_LINK_REL}>
              <ExternalLink className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Manage in Shopify
            </a>
          </Button>
        ) : null}
      </div>
    );
  }

  if (action.kind === "resolve-conflict") {
    return (
      <Button
        size={size}
        variant="outline"
        className={cn(
          "border-destructive/40 text-destructive hover:text-destructive dark:border-red-400/40 dark:text-red-300 dark:hover:text-red-200",
          className,
        )}
        onClick={onResolveConflict ?? onPublish}
        {...shared}
      >
        <AlertTriangle className="mr-1.5 h-4 w-4" aria-hidden="true" />
        {action.label}
      </Button>
    );
  }

  const publishing = action.kind === "publishing";
  return (
    <Button
      size={size}
      variant="default"
      disabled={publishing}
      aria-disabled={publishing || undefined}
      onClick={onPublish}
      className={cn("bg-primary text-primary-foreground shadow-sm", className)}
      {...shared}
    >
      {publishing ? (
        <Loader2 className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <Store className="mr-1.5 h-4 w-4" aria-hidden="true" />
      )}
      {action.label}
    </Button>
  );
}
