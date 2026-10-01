import Link from "next/link";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { PipelinePreview, ShopifyPublishCheckItem } from "@/types/api";

/** Channel action strings the app already uses, and where they lead (plan §10).
 * Anything else stays text: the client does not invent a destination. */
function actionHref(action: string, productId: string): string | null {
  switch (action) {
    case "Go to Overview":
      return `/drafts/${productId}?tab=overview`;
    case "Go to Description":
      return `/drafts/${productId}?tab=description`;
    case "Go to Media":
      return `/drafts/${productId}?tab=media`;
    case "Go to Pricing":
      return `/drafts/${productId}?tab=pricing`;
    case "Go to Shipping":
      return `/drafts/${productId}?tab=shipping`;
    case "Open Integrations":
      return "/settings/integrations";
    default:
      return null;
  }
}

/**
 * Server-authoritative readiness (plan §10). React renders what the payload
 * says and re-implements no blocker rule; it never adds a blocker the server
 * omitted.
 */
export function ReadinessPanel({
  preview,
  storeSelected,
}: {
  preview: PipelinePreview;
  storeSelected: boolean;
}) {
  const readiness = preview.channelReadiness;

  return (
    <Card data-testid="ai-studio-readiness">
      <CardHeader className="pb-3">
        <h2 className="text-base font-semibold leading-none">Readiness</h2>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {preview.pipelineBlockers.length > 0 ? (
          <ul className="space-y-1" data-testid="ai-studio-pipeline-blockers">
            {preview.pipelineBlockers.map((item) => (
              <li key={item.code} data-code={item.code} className="text-destructive">
                {item.message}
              </li>
            ))}
          </ul>
        ) : null}
        {preview.pipelineWarnings.length > 0 ? (
          <ul className="space-y-1" data-testid="ai-studio-pipeline-warnings">
            {preview.pipelineWarnings.map((item) => (
              <li key={item.code} data-code={item.code} className="text-muted-foreground">
                {item.message}
              </li>
            ))}
          </ul>
        ) : null}
        {!storeSelected ? (
          <p className="text-muted-foreground">Choose a store to check Shopify readiness.</p>
        ) : readiness === null ? (
          <p className="text-muted-foreground">Shopify readiness is not available for this store.</p>
        ) : (
          <div className="space-y-2" data-testid="ai-studio-channel-readiness">
            <p>{readiness.canPublish ? "Shopify checks passed." : "Shopify checks found blockers."}</p>
            <CheckList items={readiness.blockers} productId={preview.productId} tone="blocker" />
            <CheckList items={readiness.recommendations} productId={preview.productId} tone="recommendation" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CheckList({
  items,
  productId,
  tone,
}: {
  items: ShopifyPublishCheckItem[];
  productId: string;
  tone: "blocker" | "recommendation";
}) {
  if (items.length === 0) return null;
  return (
    <ul className="space-y-2" data-testid={`ai-studio-channel-${tone}s`}>
      {items.map((item) => {
        const href = item.action ? actionHref(item.action, productId) : null;
        return (
          <li key={`${item.code}-${item.field ?? ""}`} className="rounded-md border p-2" data-code={item.code}>
            <p className={tone === "blocker" ? "text-destructive" : undefined}>{item.message}</p>
            {item.field || item.section ? (
              <p className="text-xs text-muted-foreground">
                {[item.section, item.field].filter(Boolean).join(" · ")}
              </p>
            ) : null}
            {item.action ? (
              href ? (
                <Link className="text-xs underline" href={href}>
                  {item.action}
                </Link>
              ) : (
                <p className="text-xs text-muted-foreground">{item.action}</p>
              )
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
