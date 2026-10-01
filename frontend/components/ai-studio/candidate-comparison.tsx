import { forwardRef } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { stripHtml } from "@/lib/ai-studio/text";
import type { PipelinePreview } from "@/types/api";

/**
 * Current draft next to the AI candidate (plan §7, review finding G-1).
 *
 * The left column is the merchant's draft text, which approval never
 * changes — not "the current product" and not "what is live". Both sides
 * render as text: the supplier description through `stripHtml`, the
 * candidate as stored plain text, so neither is ever interpreted as markup.
 *
 * Below `lg` the candidate comes first, so the proposal is visible without
 * scrolling past the whole current draft.
 */
export const CandidateComparison = forwardRef<
  HTMLHeadingElement,
  { preview: PipelinePreview; approved: boolean; activeVersionNumber: number | null }
>(function CandidateComparison({ preview, approved, activeVersionNumber }, candidateHeadingRef) {
  const original = preview.original;
  const proposal = preview.proposal;

  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="ai-studio-comparison">
      <Card className="order-2 lg:order-1" data-testid="ai-studio-current">
        <CardHeader className="space-y-1">
          <h2 className="text-base font-semibold leading-none">Current draft</h2>
          <p className="text-xs text-muted-foreground">
            {activeVersionNumber !== null
              ? `Product version ${activeVersionNumber}. `
              : null}
            Approving does not change this text.
          </p>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Field label="Title" testId="ai-studio-current-title">
            {original.title ?? <Muted>No title</Muted>}
          </Field>
          <Field label="Description" testId="ai-studio-current-description">
            {original.description ? stripHtml(original.description) : <Muted>No description</Muted>}
          </Field>
        </CardContent>
      </Card>

      <Card className="order-1 lg:order-2" data-testid="ai-studio-candidate">
        <CardHeader className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2
              className="text-base font-semibold leading-none focus:outline-none"
              ref={candidateHeadingRef}
              tabIndex={-1}
              data-testid="ai-studio-candidate-heading"
            >
              AI candidate
            </h2>
            <Badge variant="outline">Version {preview.candidateVersionNumber}</Badge>
            {approved ? (
              <Badge variant="success" data-testid="ai-studio-approved-badge">
                Approved
              </Badge>
            ) : (
              <Badge variant="secondary" data-testid="ai-studio-not-approved-badge">
                Not approved
              </Badge>
            )}
          </div>
          {preview.provider ? (
            <p className="text-xs text-muted-foreground">Provider: {preview.provider}</p>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Field label="Title" testId="ai-studio-candidate-title">
            {proposal.title ?? <Muted>No title</Muted>}
          </Field>
          <Field label="Description" testId="ai-studio-candidate-description">
            {proposal.description ? (
              <span className="whitespace-pre-wrap">{proposal.description}</span>
            ) : (
              <Muted>No description</Muted>
            )}
          </Field>
          <SeoProposal preview={preview} />
        </CardContent>
      </Card>
    </div>
  );
});

function SeoProposal({ preview }: { preview: PipelinePreview }) {
  const { seoTitle, seoDescription, keywords } = preview.proposal;
  if (!seoTitle && !seoDescription && !keywords) return null;
  return (
    <div className="space-y-2 rounded-md border border-dashed p-3" data-testid="ai-studio-seo-proposal">
      <p className="text-xs font-medium text-muted-foreground">
        SEO — Proposal only — not sent to Shopify
      </p>
      {seoTitle ? <Field label="SEO title">{seoTitle}</Field> : null}
      {seoDescription ? <Field label="Meta description">{seoDescription}</Field> : null}
      {keywords ? <Field label="Keywords">{keywords}</Field> : null}
    </div>
  );
}

function Field({
  label,
  children,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div className="break-words" data-testid={testId}>
        {children}
      </div>
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>;
}
