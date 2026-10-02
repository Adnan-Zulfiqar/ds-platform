"use client";

import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { reasonOf, requestIdOf, studioErrorMessage } from "@/lib/ai-studio/errors";
import { useActivateProductVersion, useProductVersions } from "@/services/products";

/**
 * A version the pipeline will not review (plan §26).
 *
 * GET preview answers 422 `not_a_pipeline_candidate` for an unmarked legacy
 * AI row *and* for a pipeline row whose metadata is malformed, so this panel
 * does not claim the version is a clean legacy one. It offers the ordinary
 * Activate; if Activate refuses with the same reason, it stops there — no
 * retry loop, no bypass.
 */
export function LegacyVersionPanel({ productId, versionId }: { productId: string; versionId: string }) {
  const versions = useProductVersions(productId);
  const activate = useActivateProductVersion(productId);
  const version = versions.data?.items.find((row) => row.id === versionId) ?? null;
  const reason = reasonOf(activate.error);

  return (
    <Card data-testid="ai-studio-legacy-version">
      <CardHeader className="pb-3">
        <h2 className="text-base font-semibold leading-none">Not an AI Studio candidate</h2>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>This version cannot be reviewed as an AI Studio candidate.</p>
        {version ? (
          <p className="text-muted-foreground">
            Version {version.versionNumber}: {version.title ?? "Untitled"} ·{" "}
            {new Date(version.createdAt).toLocaleString()}
          </p>
        ) : null}

        {activate.isSuccess ? (
          <Alert data-testid="ai-studio-legacy-activated">
            <AlertDescription>
              Version activated. <Link className="underline" href={`/drafts/${productId}`}>Open the draft</Link>
            </AlertDescription>
          </Alert>
        ) : reason === "not_a_pipeline_candidate" ? (
          <Alert variant="destructive" data-testid="ai-studio-legacy-invalid">
            <AlertDescription>
              This version cannot be activated because its AI version metadata is invalid.
            </AlertDescription>
          </Alert>
        ) : (
          <>
            {activate.isError ? (
              <Alert variant="destructive" data-testid="ai-studio-legacy-error">
                <AlertDescription>
                  {studioErrorMessage(activate.error)}
                  {requestIdOf(activate.error) ? (
                    <span className="block text-xs">Reference: {requestIdOf(activate.error)}</span>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}
            {version && !version.active ? (
              <Button
                type="button"
                onClick={() => activate.mutate(versionId)}
                disabled={activate.isPending}
                data-testid="ai-studio-legacy-activate"
              >
                {activate.isPending ? "Activating…" : "Activate"}
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
