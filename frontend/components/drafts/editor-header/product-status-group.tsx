import { Badge } from "@/components/ui/badge";
import type { ReadinessSummary } from "@/components/drafts/editor-header/readiness";
import { cn } from "@/lib/utils";

export type LifecycleBadge =
  | "Draft"
  | "Needs Review"
  | "Ready"
  | "Publishing"
  | "Published"
  | "Publish Failed"
  | "Archived";

interface ProductStatusGroupProps {
  lifecycle: LifecycleBadge;
  readiness: ReadinessSummary;
  seoScore?: number | null;
  seoStatus?: string | null;
  publicationLabel?: string | null;
  onReadinessClick?: () => void;
  onSeoClick?: () => void;
  className?: string;
}

function lifecycleVariant(
  lifecycle: LifecycleBadge,
): "default" | "secondary" | "success" | "warning" | "destructive" | "outline" {
  switch (lifecycle) {
    case "Ready":
    case "Published":
      return "success";
    case "Needs Review":
    case "Publishing":
      return "warning";
    case "Publish Failed":
      return "destructive";
    case "Archived":
      return "outline";
    default:
      return "secondary";
  }
}

function seoTone(status: string | null | undefined): "success" | "warning" | "outline" {
  const normalised = (status ?? "").toLowerCase();
  if (normalised.includes("excellent") || normalised.includes("good")) {
    return "success";
  }
  if (normalised.includes("need") || normalised.includes("poor")) {
    return "warning";
  }
  return "outline";
}

export function ProductStatusGroup({
  lifecycle,
  readiness,
  seoScore,
  seoStatus,
  publicationLabel,
  onReadinessClick,
  onSeoClick,
  className,
}: ProductStatusGroupProps) {
  return (
    <div
      className={cn("flex flex-wrap items-center gap-1.5", className)}
      data-testid="product-status-group"
    >
      <Badge variant={lifecycleVariant(lifecycle)}>{lifecycle}</Badge>

      <button
        type="button"
        onClick={onReadinessClick}
        className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Badge variant={readiness.level === "Ready" ? "success" : "warning"}>
          {readiness.score}% Ready
        </Badge>
      </button>

      {typeof seoScore === "number" ? (
        <button
          type="button"
          onClick={onSeoClick}
          className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Badge variant={seoTone(seoStatus)}>
            SEO {seoScore}
            {seoStatus ? ` · ${seoStatus}` : ""}
          </Badge>
        </button>
      ) : null}

      {publicationLabel ? (
        <Badge variant="outline">{publicationLabel}</Badge>
      ) : null}
    </div>
  );
}
