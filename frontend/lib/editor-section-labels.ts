import type { EditorTab } from "@/components/drafts/editor-header/product-editor-tabs";

/** Exhaustive allowlist — unknown server keys must never reach the UI raw. */
const SECTION_LABELS: Record<string, string> = {
  publishing: "Store connection",
  media: "Images",
  shipping: "Shipping",
  pricing: "Price",
  inventory: "Stock",
  description: "Description",
  overview: "Product details",
  basics: "Product details",
  seo: "Search & SEO",
  variants: "Options & variants",
};

const SECTION_TAB: Record<string, EditorTab> = {
  publishing: "publishing",
  media: "media",
  shipping: "shipping",
  pricing: "pricing",
  inventory: "inventory",
  description: "description",
  overview: "overview",
  basics: "overview",
  seo: "seo",
  variants: "variants",
};

const FALLBACK_LABEL = "Review product";

export function sellerSectionLabel(section: string | null | undefined): string {
  if (!section) return FALLBACK_LABEL;
  const key = section.trim().toLowerCase();
  return SECTION_LABELS[key] ?? FALLBACK_LABEL;
}

export function editorTabForSection(
  section: string | null | undefined,
): EditorTab | null {
  if (!section) return null;
  const key = section.trim().toLowerCase();
  return SECTION_TAB[key] ?? null;
}

export function isKnownEditorSection(section: string | null | undefined): boolean {
  if (!section) return false;
  return Object.prototype.hasOwnProperty.call(
    SECTION_LABELS,
    section.trim().toLowerCase(),
  );
}
