import Link from "next/link";
import { Eye, Loader2, MoreHorizontal, Save } from "lucide-react";

import {
  PublishAction,
  type PublishActionKind,
} from "@/components/drafts/editor-header/publish-action";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface MobileEditorActionBarProps {
  productId: string;
  saving: boolean;
  saveDisabled: boolean;
  publishKind: PublishActionKind;
  issueCount: number;
  disabledPublishReason?: string | null;
  storefrontUrl?: string | null;
  hasSyncedListing?: boolean;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onOpenMore?: () => void;
}

export function MobileEditorActionBar({
  productId,
  saving,
  saveDisabled,
  publishKind,
  issueCount,
  disabledPublishReason,
  storefrontUrl,
  hasSyncedListing = false,
  onPreview,
  onSave,
  onPublish,
}: MobileEditorActionBarProps) {
  const productHref = `/products/${productId}`;
  const showOverflowPrimary = publishKind === "view_product";

  return (
    <div
      className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur md:hidden"
      data-testid="mobile-editor-action-bar"
    >
      <div className="mx-auto flex max-w-lg items-center gap-2">
        {showOverflowPrimary ? (
          <>
            <Button asChild className="min-h-11 flex-1">
              <Link href={productHref}>View product</Link>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="icon"
                  className="min-h-11 min-w-11 shrink-0"
                  aria-label="More actions"
                  data-testid="mobile-completion-overflow"
                >
                  <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={onPreview}>Preview</DropdownMenuItem>
                {!saveDisabled || saving ? (
                  <DropdownMenuItem onClick={onSave}>
                    {saving ? "Saving…" : "Save draft"}
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onClick={onPublish}>Review & publish</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        ) : (
          <>
            {!saveDisabled || saving ? (
              <Button
                variant="outline"
                className="min-h-11 flex-1"
                disabled={saving}
                onClick={onSave}
                data-testid="save-draft"
              >
                {saving ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                ) : (
                  <Save className="mr-1.5 h-4 w-4" aria-hidden="true" />
                )}
                {saving ? "Saving…" : "Save draft"}
              </Button>
            ) : null}
            <Button variant="outline" className="min-h-11 flex-1" onClick={onPreview}>
              <Eye className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Preview
            </Button>
            <div className="flex-1 [&_button]:min-h-11 [&_button]:w-full">
              <PublishAction
                kind={publishKind}
                issueCount={issueCount}
                disabledReason={disabledPublishReason}
                storefrontUrl={storefrontUrl}
                productHref={productHref}
                onPublish={onPublish}
                size="default"
                intent="navigate"
              />
            </div>
          </>
        )}
      </div>
      {hasSyncedListing ? (
        <p className="mx-auto mt-2 max-w-lg text-center text-xs text-muted-foreground">
          {publishKind === "review_changes"
            ? "You have changes that are not on Shopify yet."
            : null}
        </p>
      ) : null}
    </div>
  );
}
