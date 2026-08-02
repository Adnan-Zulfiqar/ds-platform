"use client";

import { Loader2, Sparkles } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useOptimizeProduct } from "@/services/products";

/**
 * Trigger AI optimisation for one product.
 *
 * A single button, not a form — Phase 9 stage 3 keeps the UI to a foundation
 * (button, status, history) rather than a full editor with tone/keyword
 * controls. Uses `StubProvider`: no real `AI_PROVIDER` is configured on this
 * platform yet, so every result is deterministic, obviously-synthetic text.
 */
export function OptimizeProductButton({ productId }: { productId: string }) {
  const optimize = useOptimizeProduct(productId);

  return (
    <div className="space-y-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() => optimize.mutate({})}
        disabled={optimize.isPending}
      >
        {optimize.isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            Optimizing…
          </>
        ) : (
          <>
            <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" />
            Optimize with AI
          </>
        )}
      </Button>

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
