"use client";

import { Loader2, Sparkles } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useOptimizeProduct } from "@/services/products";

export function OptimizeProductButton({
  productId,
  compact = false,
}: {
  productId: string;
  /**
   * Icon only, named for assistive technology and explained by a tooltip.
   * The catalogue rows use this so the primary action stays the only
   * labelled button in the row (UX-L2D-04).
   */
  compact?: boolean;
}) {
  const optimize = useOptimizeProduct(productId);
  const label = optimize.isPending ? "Optimizing…" : "Optimize with AI";

  const button = (
    <Button
      size={compact ? "icon" : "sm"}
      variant={compact ? "ghost" : "outline"}
      className={compact ? "h-9 w-9" : undefined}
      onClick={() => optimize.mutate({})}
      disabled={optimize.isPending}
      aria-label={compact ? label : undefined}
    >
      {optimize.isPending ? (
        <Loader2 className={compact ? "h-4 w-4 animate-spin" : "mr-2 h-4 w-4 animate-spin"} aria-hidden="true" />
      ) : (
        <Sparkles className={compact ? "h-4 w-4" : "mr-2 h-4 w-4"} aria-hidden="true" />
      )}
      {!compact && label}
    </Button>
  );

  return (
    <div className="space-y-2">
      {compact ? (
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent>{label}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}

      {optimize.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {optimize.error instanceof Error
              ? optimize.error.message
              : "Optimization failed."}
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
