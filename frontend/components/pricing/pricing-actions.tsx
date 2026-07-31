"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useApplyPricing, usePreviewPricing } from "@/services/pricing";

export function PricingActions() {
  const preview = usePreviewPricing();
  const apply = useApplyPricing();
  const [summary, setSummary] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {summary && (
        <p className="text-sm text-muted-foreground" role="status">
          {summary}
        </p>
      )}
      <Button
        variant="outline"
        disabled={preview.isPending}
        onClick={() => {
          preview.mutate(
            {},
            {
              onSuccess: (result) => {
                setSummary(
                  `Preview: ${result.wouldChange} of ${result.items.length} would change.`,
                );
              },
              onError: (err) => {
                setSummary(err instanceof Error ? err.message : "Preview failed.");
              },
            },
          );
        }}
      >
        {preview.isPending ? "Previewing…" : "Preview"}
      </Button>
      <Button
        disabled={apply.isPending}
        onClick={() => {
          apply.mutate(
            {},
            {
              onSuccess: (changes) => {
                setSummary(`Applied ${changes.length} price change(s).`);
              },
              onError: (err) => {
                setSummary(err instanceof Error ? err.message : "Apply failed.");
              },
            },
          );
        }}
      >
        {apply.isPending ? "Applying…" : "Apply rules"}
      </Button>
    </div>
  );
}
