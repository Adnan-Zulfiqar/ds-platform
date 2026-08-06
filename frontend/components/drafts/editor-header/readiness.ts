import type { ProductDetail } from "@/types/api";

export interface ReadinessSummary {
  score: number;
  level: "Ready" | "Needs Review" | "Incomplete" | "Blocked";
  issues: string[];
}

/**
 * Lightweight client-side readiness for header badges.
 *
 * Channel validation still runs at publish time — this only surfaces obvious
 * content gaps so merchants see progress without a separate API call.
 */
export function readinessFor(product: ProductDetail): ReadinessSummary {
  const issues: string[] = [];
  let score = 0;

  if (product.title.trim().length >= 8) score += 20;
  else issues.push("Title is too short");

  if (product.description && product.description.replace(/<[^>]+>/g, "").trim())
    score += 20;
  else issues.push("Description is missing");

  if (product.images.length > 0) score += 15;
  else issues.push("Add at least one image");

  if (product.variants.length > 0) score += 15;
  else issues.push("No variants imported");

  if (product.costPriceMin) score += 10;
  else issues.push("Supplier cost missing");

  if (product.seoTitle) score += 10;
  else issues.push("SEO title missing");

  if (product.slug) score += 10;
  else issues.push("URL slug missing");

  const level: ReadinessSummary["level"] =
    issues.length === 0
      ? "Ready"
      : score >= 60
        ? "Needs Review"
        : score >= 30
          ? "Incomplete"
          : "Blocked";

  return { score, level, issues };
}

export function estimateMarginPercent(
  sellPrice: string | null | undefined,
  costPrice: string | null | undefined,
): number | null {
  if (!sellPrice || !costPrice) return null;
  const sell = Number(sellPrice);
  const cost = Number(costPrice);
  if (!Number.isFinite(sell) || !Number.isFinite(cost) || sell <= 0) return null;
  return Math.round(((sell - cost) / sell) * 100);
}

const SHIP_TO_LABELS: Record<string, string> = {
  GB: "United Kingdom",
  UK: "United Kingdom",
  US: "United States",
};

export function shipToLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  const normalised = code.trim().toUpperCase();
  return SHIP_TO_LABELS[normalised] ?? normalised;
}

/**
 * Locale-aware supplier sync timestamp (UK: 6 Aug 2026 at 21:55).
 */
export function formatSupplierSyncedAt(
  value: string | null | undefined,
  locale?: string | null,
): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;

  const resolved =
    locale ||
    (typeof navigator !== "undefined" ? navigator.language : "en-GB");

  const dayMonth = new Intl.DateTimeFormat(resolved, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);

  const time = new Intl.DateTimeFormat(resolved, {
    hour: "numeric",
    minute: "2-digit",
    hour12: !resolved.toLowerCase().startsWith("en-gb"),
  }).format(date);

  return `${dayMonth} at ${time}`;
}
