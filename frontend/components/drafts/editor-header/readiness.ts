import type { ProductDetail } from "@/types/api";

export type ReadinessSeverity = "required" | "recommended";

export interface ReadinessIssue {
  id: string;
  message: string;
  effect: string;
  /** Existing editor tab to open when the merchant chooses to fix this. */
  tab:
    | "overview"
    | "description"
    | "media"
    | "variants"
    | "pricing"
    | "seo"
    | "publishing"
    | "shipping";
  severity: ReadinessSeverity;
}

export interface ReadinessSummary {
  score: number;
  level: "Ready" | "Needs Review" | "Incomplete" | "Blocked";
  /** Plain-language issue strings (backward compatible with existing callers). */
  issues: string[];
  items: ReadinessIssue[];
}

/**
 * Lightweight client-side readiness for header and checklist chrome.
 *
 * Channel validation still runs at publish time — this only surfaces obvious
 * content gaps so merchants see progress without a separate API call.
 *
 * Severity is presentation-only over the same client checks: SEO/slug gaps are
 * shown as recommended; content gaps that block a sensible publish attempt are
 * required. The API does not classify these.
 */
export function readinessFor(product: ProductDetail): ReadinessSummary {
  const items: ReadinessIssue[] = [];
  let score = 0;

  if (product.title.trim().length >= 8) score += 20;
  else {
    items.push({
      id: "title",
      message: "Add a clearer product title",
      effect: "Shoppers need a title before this can be published.",
      tab: "overview",
      severity: "required",
    });
  }

  if (product.description && product.description.replace(/<[^>]+>/g, "").trim())
    score += 20;
  else {
    items.push({
      id: "description",
      message: "Add a product description",
      effect: "A blank description looks incomplete on your store.",
      tab: "description",
      severity: "required",
    });
  }

  if (product.images.length > 0) score += 15;
  else {
    items.push({
      id: "images",
      message: "Add at least one product image",
      effect: "Stores expect a main image before listing.",
      tab: "media",
      severity: "required",
    });
  }

  if (product.variants.length > 0) score += 15;
  else {
    items.push({
      id: "variants",
      message: "Import product options",
      effect: "Variants carry size, colour and stock from the supplier.",
      tab: "variants",
      severity: "required",
    });
  }

  if (product.costPriceMin) score += 10;
  else {
    items.push({
      id: "cost",
      message: "Supplier cost is missing",
      effect: "Profit tools need a cost before you confirm your selling price.",
      tab: "pricing",
      severity: "required",
    });
  }

  if (product.seoTitle) score += 10;
  else {
    items.push({
      id: "seo-title",
      message: "Add a search title",
      effect: "Helps search results, but is not a hard publish gate here.",
      tab: "seo",
      severity: "recommended",
    });
  }

  if (product.slug) score += 10;
  else {
    items.push({
      id: "slug",
      message: "Add a product URL",
      effect: "Gives the listing a stable link on your store.",
      tab: "seo",
      severity: "recommended",
    });
  }

  const requiredCount = items.filter((i) => i.severity === "required").length;
  const level: ReadinessSummary["level"] =
    items.length === 0
      ? "Ready"
      : requiredCount === 0
        ? "Needs Review"
        : score >= 60
          ? "Needs Review"
          : score >= 30
            ? "Incomplete"
            : "Blocked";

  return {
    score,
    level,
    issues: items.map((item) => item.message),
    items,
  };
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
  CN: "China",
};

export function shipToLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  const normalised = code.trim().toUpperCase();
  return SHIP_TO_LABELS[normalised] ?? normalised;
}

/**
 * Locale-aware supplier sync timestamp (UK: 10 Aug 2026, 18:34).
 */
export function formatSupplierSyncedAt(
  value: string | null | undefined,
  locale?: string | null,
): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;

  const resolved = locale || "en-GB";

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

  return `${dayMonth}, ${time}`;
}

/** Relative time for “Last checked”, with exact stamp as secondary detail. */
export function formatRelativeCheckedAt(
  value: string | null | undefined,
  now: Date = new Date(),
): { relative: string; exact: string } | null {
  const exact = formatSupplierSyncedAt(value);
  if (!value || !exact) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const deltaMs = now.getTime() - date.getTime();
  const minutes = Math.round(deltaMs / 60_000);
  let relative: string;
  if (minutes < 1) relative = "Just now";
  else if (minutes < 60) relative = `${minutes} min ago`;
  else if (minutes < 60 * 24) {
    const hours = Math.round(minutes / 60);
    relative = `${hours} hour${hours === 1 ? "" : "s"} ago`;
  } else {
    const days = Math.round(minutes / (60 * 24));
    relative = `${days} day${days === 1 ? "" : "s"} ago`;
  }
  return { relative, exact };
}
