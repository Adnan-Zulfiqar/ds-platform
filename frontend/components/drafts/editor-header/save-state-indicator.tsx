import { AlertCircle, Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export type SaveState = "idle" | "saving" | "saved" | "error" | "conflict";

interface SaveStateIndicatorProps {
  dirty: boolean;
  saveState: SaveState;
  className?: string;
}

export function SaveStateIndicator({
  dirty,
  saveState,
  className,
}: SaveStateIndicatorProps) {
  let label = "Saved just now";
  let tone: "muted" | "warn" | "error" | "ok" = "muted";
  let Icon: typeof Check | null = Check;

  if (saveState === "saving") {
    label = "Saving…";
    tone = "muted";
    Icon = Loader2;
  } else if (saveState === "error") {
    label = "Save failed";
    tone = "error";
    Icon = AlertCircle;
  } else if (saveState === "conflict") {
    label = "Conflict detected";
    tone = "error";
    Icon = AlertCircle;
  } else if (dirty) {
    label = "Unsaved changes";
    tone = "warn";
    Icon = null;
  } else if (saveState === "saved") {
    label = "Saved just now";
    tone = "ok";
    Icon = Check;
  } else {
    label = "All changes saved";
    tone = "muted";
    Icon = Check;
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium",
        tone === "warn" && "text-amber-700 dark:text-amber-400",
        tone === "error" && "text-destructive",
        tone === "ok" && "text-emerald-700 dark:text-emerald-400",
        tone === "muted" && "text-muted-foreground",
        className,
      )}
      data-testid="draft-save-state"
      aria-live="polite"
    >
      {Icon ? (
        <Icon
          className={cn("h-3.5 w-3.5", saveState === "saving" && "animate-spin")}
          aria-hidden="true"
        />
      ) : null}
      {label}
    </span>
  );
}
