import { ExternalLink, Loader2, Store } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type PublishActionKind =
  | "publish"
  | "review_items"
  | "publishing"
  | "view_store"
  | "push_updates"
  | "retry";

export type PublishActionIntent = "navigate" | "publish";

interface PublishActionProps {
  kind: PublishActionKind;
  issueCount?: number;
  disabledReason?: string | null;
  storefrontUrl?: string | null;
  adminUrl?: string | null;
  onPublish: () => void;
  className?: string;
  size?: "default" | "sm";
  /** Header/mobile controls navigate to Review & publish; only the panel publishes. */
  intent?: PublishActionIntent;
}

function labelFor(
  kind: PublishActionKind,
  issueCount: number,
  intent: PublishActionIntent,
): string {
  if (intent === "navigate") {
    switch (kind) {
      case "review_items":
        return `Review ${issueCount} item${issueCount === 1 ? "" : "s"}`;
      case "publishing":
        return "Publishing…";
      case "view_store":
        return "View in store";
      case "retry":
      case "push_updates":
      case "publish":
        return issueCount > 0
          ? `Review ${issueCount} item${issueCount === 1 ? "" : "s"}`
          : "Review & publish";
      default:
        return "Review & publish";
    }
  }

  switch (kind) {
    case "review_items":
      return `Review ${issueCount} item${issueCount === 1 ? "" : "s"}`;
    case "publishing":
      return "Publishing…";
    case "view_store":
      return "View in store";
    case "push_updates":
      return "Push updates";
    case "retry":
      return "Try publishing again";
    default:
      return "Publish to store";
  }
}

export function PublishAction({
  kind,
  issueCount = 0,
  disabledReason,
  storefrontUrl,
  adminUrl,
  onPublish,
  className,
  size = "default",
  intent = "publish",
}: PublishActionProps) {
  const label = labelFor(kind, issueCount, intent);
  // Presentation checklist items must not hard-disable this control — they only
  // change the label to "Review N items". Only an in-flight publish is disabled,
  // so a double-submit cannot race the channel request.
  const disabled = kind === "publishing";
  const isPrimary = kind !== "view_store";

  const button = (
    <Button
      size={size}
      variant={isPrimary ? "default" : "outline"}
      disabled={disabled}
      onClick={() => {
        if (kind === "view_store" && storefrontUrl) {
          window.open(storefrontUrl, "_blank", "noopener,noreferrer");
          return;
        }
        onPublish();
      }}
      className={cn(
        isPrimary && "bg-primary text-primary-foreground shadow-sm",
        className,
      )}
      data-testid="publish-action"
      data-publish-intent={intent}
      aria-disabled={disabled || undefined}
    >
      {kind === "publishing" ? (
        <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden="true" />
      ) : kind === "view_store" ? (
        <ExternalLink className="mr-1.5 h-4 w-4" aria-hidden="true" />
      ) : (
        <Store className="mr-1.5 h-4 w-4" aria-hidden="true" />
      )}
      {label}
    </Button>
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      {disabled && disabledReason ? (
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="inline-flex">
                {button}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{disabledReason}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        button
      )}

      {kind === "view_store" && adminUrl ? (
        <Button
          size={size}
          variant="ghost"
          asChild
          data-testid="manage-in-shopify"
        >
          <a href={adminUrl} target="_blank" rel="noopener noreferrer">
            Manage in Shopify
          </a>
        </Button>
      ) : null}
    </div>
  );
}
