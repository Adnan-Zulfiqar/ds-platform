import { Eye, Loader2, Save } from "lucide-react";

import { PublishAction } from "@/components/drafts/editor-header/publish-action";
import { Button } from "@/components/ui/button";
import type { NextAction } from "@/lib/editor-lifecycle";

interface MobileEditorActionBarProps {
  productId: string;
  saving: boolean;
  saveDisabled: boolean;
  action: NextAction;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
  onResolveConflict: () => void;
}

/**
 * The fixed bottom bar below `md`.
 *
 * The primary action gets the width: at 390px three equal buttons left
 * "Review & publish" wrapping onto two lines, so Preview is icon-only here
 * (its name stays for assistive technology) and Save appears only while
 * there is something to save. Lifecycle wording is not repeated in the
 * bar — the sticky header above already shows the badge and save state at
 * every width, so the bar stays a row of actions.
 */
export function MobileEditorActionBar({
  productId,
  saving,
  saveDisabled,
  action,
  onPreview,
  onSave,
  onPublish,
  onResolveConflict,
}: MobileEditorActionBarProps) {
  return (
    <div
      className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur md:hidden"
      data-testid="mobile-editor-action-bar"
    >
      <div className="mx-auto flex max-w-lg items-center gap-2">
        {!saveDisabled || saving ? (
          <Button
            variant="outline"
            className="min-h-11 shrink-0"
            disabled={saving}
            onClick={onSave}
            data-testid="save-draft"
          >
            {saving ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Save className="mr-1.5 h-4 w-4" aria-hidden="true" />
            )}
            {saving ? "Saving…" : "Save"}
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="icon"
          className="h-11 w-11 shrink-0"
          onClick={onPreview}
          aria-label="Preview"
        >
          <Eye className="h-4 w-4" aria-hidden="true" />
        </Button>
        <div className="min-w-0 flex-1 [&_a]:w-full [&_button]:w-full [&_button]:min-h-11 [&>div]:flex-nowrap">
          <PublishAction
            action={action}
            productId={productId}
            onPublish={onPublish}
            onResolveConflict={onResolveConflict}
            size="default"
          />
        </div>
      </div>
    </div>
  );
}
