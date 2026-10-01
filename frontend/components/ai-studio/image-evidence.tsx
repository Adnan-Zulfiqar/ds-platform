import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { PipelineImageAnalysisItem } from "@/types/api";

/**
 * Stage 6 image evidence from the preview payload (plan §9). The browser
 * never re-fetches supplier images to recompute anything.
 *
 * The row is decided from the outer `status`, in a fixed order, so a newer
 * backend status renders as "Analysis unavailable" instead of throwing.
 */
export function ImageEvidence({ images }: { images: PipelineImageAnalysisItem[] }) {
  return (
    <Card data-testid="ai-studio-images">
      <CardHeader className="pb-3">
        <h2 className="text-base font-semibold leading-none">Image evidence</h2>
        <p className="text-xs text-muted-foreground">
          Captions and alt text are proposals only — not sent to Shopify.
        </p>
      </CardHeader>
      <CardContent>
        {images.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="ai-studio-images-empty">
            Not analyzed
          </p>
        ) : (
          <ul className="space-y-3">
            {[...images]
              .sort((a, b) => a.position - b.position)
              .map((image) => (
                <ImageRow key={image.imageId} image={image} />
              ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ImageRow({ image }: { image: PipelineImageAnalysisItem }) {
  const analysis = image.analysis;
  const errorCode = image.errorCode ?? analysis?.errorCode ?? null;
  let state: "unavailable" | "not-analyzed" | "succeeded" | "checks-only";

  if (image.status === "fetchFailed" || image.status === "decodeFailed") state = "unavailable";
  else if (image.status === "unknown" || analysis === null) state = "not-analyzed";
  else if (image.status === "succeeded") state = "succeeded";
  else if (image.status === "checksOnly") state = "checks-only";
  else state = "unavailable";

  return (
    <li
      className="space-y-1 rounded-md border p-3 text-sm"
      data-testid="ai-studio-image-row"
      data-state={state}
    >
      <p className="font-medium">Image {image.position + 1}</p>
      {state === "unavailable" ? (
        <p>
          Analysis unavailable
          {errorCode ? <span className="text-muted-foreground"> ({errorCode})</span> : null}
        </p>
      ) : null}
      {state === "not-analyzed" ? <p className="text-muted-foreground">Not analyzed</p> : null}
      {(state === "succeeded" || state === "checks-only") && analysis?.checks ? (
        <ul className="text-xs text-muted-foreground" data-testid="ai-studio-image-checks">
          {analysis.checks.blur.applicable ? (
            <li>{analysis.checks.blur.isBlurry ? "Looks blurry" : "Sharp enough"}</li>
          ) : null}
          {analysis.checks.duplicates.applicable ? (
            <li>
              {analysis.checks.duplicates.duplicateOfImageIds.length > 0
                ? "Duplicate of another image"
                : "No duplicate found"}
            </li>
          ) : null}
          {analysis.checks.watermark.applicable ? <li>Watermark: {analysis.checks.watermark.reason}</li> : null}
        </ul>
      ) : null}
      {state === "checks-only" ? (
        <p className="text-xs text-muted-foreground">Caption and alt text unavailable.</p>
      ) : null}
      {state === "succeeded" && analysis ? (
        <div className="space-y-1" data-testid="ai-studio-image-proposals">
          {analysis.isSynthetic ? <Badge variant="warning">Test caption</Badge> : null}
          {analysis.captionProposal ? <p>Caption: {analysis.captionProposal}</p> : null}
          {analysis.altTextProposal ? <p>Alt text: {analysis.altTextProposal}</p> : null}
        </div>
      ) : null}
    </li>
  );
}
