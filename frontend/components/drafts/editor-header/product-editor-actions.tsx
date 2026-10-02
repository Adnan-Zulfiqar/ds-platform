import { Eye, Loader2, Save } from "lucide-react";

import { ProductActionsMenu } from "@/components/drafts/editor-header/product-actions-menu";
import { PublishAction } from "@/components/drafts/editor-header/publish-action";
import { Button } from "@/components/ui/button";
import type { NextAction } from "@/lib/editor-lifecycle";
import { cn } from "@/lib/utils";

interface ProductEditorActionsProps {
  productId: string;
  saving: boolean;
  saveDisabled: boolean;
  action: NextAction;
  adminUrl?: string | null;
  refreshing: boolean;
  hasExternalUrl: boolean;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onResolveConflict: () => void;
  onRefresh: () => void;
  /** Review route for this product in AI Studio (replaces the legacy Optimize). */
  aiStudioHref: string;
  onOpenAliExpress: () => void;
  onViewHistory: () => void;
  className?: string;
}

export function ProductEditorActions({
  productId,
  saving,
  saveDisabled,
  action,
  adminUrl,
  refreshing,
  hasExternalUrl,
  onPreview,
  onSave,
  onPublish,
  onResolveConflict,
  onRefresh,
  aiStudioHref,
  onOpenAliExpress,
  onViewHistory,
  className,
}: ProductEditorActionsProps) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-wrap items-center justify-end gap-2",
        className,
      )}
      data-testid="product-editor-actions"
    >
      <Button variant="outline" size="sm" onClick={onPreview}>
        <Eye className="mr-1.5 h-4 w-4" aria-hidden="true" />
        Preview
      </Button>

      {/* Manual save only when there is something to save or retry — never a
          disabled “Saved” button that looks like an action. */}
      {!saveDisabled || saving ? (
        <Button
          variant="outline"
          size="sm"
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

      <PublishAction
        action={action}
        productId={productId}
        adminUrl={adminUrl}
        onPublish={onPublish}
        onResolveConflict={onResolveConflict}
        size="sm"
      />

      <ProductActionsMenu
        refreshing={refreshing}
        hasExternalUrl={hasExternalUrl}
        onRefresh={onRefresh}
        aiStudioHref={aiStudioHref}
        onOpenAliExpress={onOpenAliExpress}
        onViewHistory={onViewHistory}
      />
    </div>
  );
}
