import { cn } from "@/lib/utils";

interface ProductMetricsProps {
  seoScore?: number | null;
  marginPercent?: number | null;
  shopifyLabel?: string | null;
  onSeoClick?: () => void;
  onMarginClick?: () => void;
  onShopifyClick?: () => void;
  className?: string;
}

function MetricChip({
  label,
  onClick,
  testId,
}: {
  label: string;
  onClick?: () => void;
  testId: string;
}) {
  const className =
    "rounded-md border border-border/70 bg-muted/30 px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className} data-testid={testId}>
        {label}
      </button>
    );
  }

  return (
    <span className={className} data-testid={testId}>
      {label}
    </span>
  );
}

/**
 * Compact commercial summary — chips, not dashboard cards.
 *
 * Intentionally omits any “% Ready” / publish-completion claim. Client
 * readiness is presentation advice only; channel checks decide publishing.
 */
export function ProductMetrics({
  seoScore,
  marginPercent,
  shopifyLabel,
  onSeoClick,
  onMarginClick,
  onShopifyClick,
  className,
}: ProductMetricsProps) {
  return (
    <div
      className={cn("flex flex-wrap items-center gap-1.5", className)}
      data-testid="product-metrics"
    >
      {typeof seoScore === "number" ? (
        <MetricChip
          label={`SEO ${seoScore}`}
          onClick={onSeoClick}
          testId="metric-seo"
        />
      ) : null}
      {typeof marginPercent === "number" ? (
        <MetricChip
          label={`Margin ${marginPercent}%`}
          onClick={onMarginClick}
          testId="metric-margin"
        />
      ) : null}
      {shopifyLabel ? (
        <MetricChip
          label={`Shopify: ${shopifyLabel}`}
          onClick={onShopifyClick}
          testId="metric-shopify"
        />
      ) : null}
    </div>
  );
}
