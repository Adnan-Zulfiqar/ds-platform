import { Eye, Loader2, Save } from "lucide-react";

import {
  PublishAction,
  type PublishActionKind,
} from "@/components/drafts/editor-header/publish-action";
import { Button } from "@/components/ui/button";

interface MobileEditorActionBarProps {
  saving: boolean;
  saveDisabled: boolean;
  publishKind: PublishActionKind;
  issueCount: number;
  disabledPublishReason?: string | null;
  storefrontUrl?: string | null;
  onPreview: () => void;
  onSave: () => void;
  onPublish: () => void;
}

export function MobileEditorActionBar({
  saving,
  saveDisabled,
  publishKind,
  issueCount,
  disabledPublishReason,
  storefrontUrl,
  onPreview,
  onSave,
  onPublish,
}: MobileEditorActionBarProps) {
  return (
    <div
      className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur md:hidden"
      data-testid="mobile-editor-action-bar"
    >
      <div className="mx-auto flex max-w-lg items-center gap-2">
        <Button
          variant="outline"
          className="min-h-11 flex-1"
          disabled={saveDisabled || saving}
          onClick={onSave}
          data-testid="save-draft"
        >
          {saving ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Save className="mr-1.5 h-4 w-4" aria-hidden="true" />
          )}
          Save
        </Button>
        <Button
          variant="outline"
          className="min-h-11 flex-1"
          onClick={onPreview}
        >
          <Eye className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Preview
        </Button>
        <div className="flex-1 [&_button]:min-h-11 [&_button]:w-full">
          <PublishAction
            kind={publishKind}
            issueCount={issueCount}
            disabledReason={disabledPublishReason}
            storefrontUrl={storefrontUrl}
            onPublish={onPublish}
            size="default"
          />
        </div>
      </div>
    </div>
  );
}
