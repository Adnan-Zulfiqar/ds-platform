import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { PipelinePreview, ProductVersionQualityBreakdown } from "@/types/api";

/**
 * Stage 5 score evidence (plan §8).
 *
 * The rubric is deterministic and always compares the candidate with the
 * **original supplier snapshot**, never with the current draft or a previous
 * AI version — so the labels say "original baseline", and nothing here is
 * called confidence, accuracy or a prediction.
 */
export function QualityPanel({ preview }: { preview: PipelinePreview }) {
  const { qualityScore, qualityBaseline, qualityDelta, qualityScoreVersion, qualityBreakdown } =
    preview;

  return (
    <Card data-testid="ai-studio-quality">
      <CardHeader className="pb-3">
        <h2 className="text-base font-semibold leading-none">Optimization score</h2>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <dl className="grid gap-3 sm:grid-cols-3">
          <Metric label="Optimization score" testId="ai-studio-quality-score">
            {qualityScore === null ? "Score unavailable" : String(qualityScore)}
          </Metric>
          <Metric label="Original baseline score" testId="ai-studio-quality-baseline">
            {qualityBaseline === null ? "Score unavailable" : String(qualityBaseline.score)}
          </Metric>
          <Metric label="Change vs original" testId="ai-studio-quality-delta">
            {qualityDelta === null ? "Score unavailable" : signed(qualityDelta)}
          </Metric>
        </dl>
        {qualityDelta !== null ? (
          <p data-testid="ai-studio-quality-delta-copy">{deltaCopy(qualityDelta)}</p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {qualityBaseline ? `Original baseline version ${qualityBaseline.versionNumber}. ` : null}
          {qualityScoreVersion !== null ? `Score version ${qualityScoreVersion}. ` : null}
          The score describes the text against a fixed rubric. It does not predict sales.
        </p>
        {qualityBreakdown === null ? (
          <p className="text-muted-foreground">Score breakdown unavailable</p>
        ) : (
          <Breakdown breakdown={qualityBreakdown} />
        )}
      </CardContent>
    </Card>
  );
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function deltaCopy(delta: number): string {
  if (delta > 0) return "Higher than the original baseline";
  if (delta < 0) return "Lower than the original baseline";
  return "Same as the original baseline";
}

function yesNo(value: boolean): string {
  return value ? "Yes" : "No";
}

function Breakdown({ breakdown }: { breakdown: ProductVersionQualityBreakdown }) {
  const { title, description, repetition, keywordCoverage } = breakdown.dimensions;
  return (
    <div className="space-y-3" data-testid="ai-studio-quality-breakdown">
      <p>
        Points earned: {breakdown.earned} of {breakdown.applicableMax}
      </p>
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        <Row label="Title">
          {title.applicable ? `${title.points} of ${title.max} · ${title.length} characters` : "Not applicable"}
        </Row>
        <Row label="Description">
          {description.applicable
            ? `${description.points} of ${description.max} · ${description.length} characters`
            : "Not applicable"}
        </Row>
        <Row label="Repetition">
          {repetition.applicable ? `${repetition.points} of ${repetition.max}` : "Not applicable"}
        </Row>
        <Row label="Keyword coverage">
          {keywordCoverage.applicable
            ? `${keywordCoverage.points} of ${keywordCoverage.max} · ${keywordCoverage.matched} of ${keywordCoverage.total} keywords`
            : "Not applicable"}
        </Row>
      </dl>
      <dl className="grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2">
        <Row label="Title not stuffed">{yesNo(repetition.checks.titleNotStuffed)}</Row>
        <Row label="Description not phrase-stuffed">
          {yesNo(repetition.checks.descriptionNotPhraseStuffed)}
        </Row>
        <Row label="Description not dominated by one phrase">
          {yesNo(repetition.checks.descriptionNotDominated)}
        </Row>
        <Row label="Description distinct from title">
          {yesNo(repetition.checks.descriptionDistinctFromTitle)}
        </Row>
      </dl>
      {keywordCoverage.applicable ? (
        <p className="text-xs text-muted-foreground">
          Keywords from {keywordCoverage.source ?? "no source"}: {keywordCoverage.keywords.join(", ") || "none"}
          {keywordCoverage.truncated ? " (list truncated)" : ""}
        </p>
      ) : null}
      {breakdown.seoFormat ? (
        <dl className="grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
          <Row label="SEO title within bound">{yesNo(breakdown.seoFormat.seoTitleWithinRequestedBound)}</Row>
          <Row label="SEO description within bound">
            {yesNo(breakdown.seoFormat.seoDescriptionWithinRequestedBound)}
          </Row>
          <Row label="Keywords present">{yesNo(breakdown.seoFormat.keywordsPresent)}</Row>
        </dl>
      ) : null}
    </div>
  );
}

function Metric({ label, children, testId }: { label: string; children: React.ReactNode; testId: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-semibold" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}
