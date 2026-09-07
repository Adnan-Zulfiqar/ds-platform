import { Eye, Loader2, Save } from "lucide-react";

import { ProductActionsMenu } from "@/components/drafts/editor-header/product-actions-menu";
import {
  PublishAction,
  type PublishActionKind,
} from "@/components/drafts/editor-header/publish-action";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ProductEditorActionsProps {
  saving: boolean;
  saveDisabled: boolean;
  dirty: boolean;
  publishKind: PublishActionKind;
  issueCount: number;
  disabledPublishReason?: string | null;
  storefrontUrl?: string | null;
  adminUrl?: string | null;
  refreshing: boolean;
  optimizing: boolean;
  hasExternalUrl: boolean;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onRefresh: () => void;
  onOptimize: () => void;
  onOpenAliExpress: () => void;
  onViewHistory: () => void;
  onGoHistoryTab: () => void;
  className?: string;
}

export function ProductEditorActions({
  saving,
  saveDisabled,
  dirty: _dirty,
  publishKind,
  issueCount,
  disabledPublishReason,
  storefrontUrl,
  adminUrl,
  refreshing,
  optimizing,
  hasExternalUrl,
  onPreview,
  onSave,
  onPublish,
  onRefresh,
  onOptimize,
  onOpenAliExpress,
  onViewHistory,
  onGoHistoryTab,
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
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Save className="mr-1.5 h-4 w-4" aria-hidden="true" />
          )}
          {saving ? "Saving…" : "Save draft"}
        </Button>
      ) : null}

      <PublishAction
        kind={publishKind}
        issueCount={issueCount}
        disabledReason={disabledPublishReason}
        storefrontUrl={storefrontUrl}
        adminUrl={adminUrl}
        onPublish={onPublish}
        size="sm"
        intent="navigate"
      />

      <ProductActionsMenu
        refreshing={refreshing}
        optimizing={optimizing}
        hasExternalUrl={hasExternalUrl}
        onRefresh={onRefresh}
        onOptimize={onOptimize}
        onOpenAliExpress={onOpenAliExpress}
        onViewHistory={onViewHistory}
        onGoHistoryTab={onGoHistoryTab}
      />
    </div>
  );
}
