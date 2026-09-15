import type { EditorTab } from "@/components/drafts/editor-header/product-editor-tabs";

/**
 * Merchant-facing names for the `section` keys the publish-readiness API
 * attaches to blockers and recommendations.
 *
 * Reused from the reviewed historical UX-L2C branch (UX-L2D-GATE-04). The
 * API's keys are its own vocabulary ("basics", "publishing"); showing them
 * raw under a blocker read as "Section: basics", which names nothing the
 * merchant can see. An exhaustive allowlist rather than a title-case of the
 * key, so an unknown key falls back to a neutral label instead of leaking.
 */
const SECTION_LABELS: Record<string, string> = {
  publishing: "Store connection",
  media: "Images & video",
  shipping: "Shipping",
  pricing: "Price & profit",
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

export function sellerSectionLabel(section: string | null | undefined): string | null {
  if (!section) return null;
  return SECTION_LABELS[section.trim().toLowerCase()] ?? null;
}

/** The editor tab a readiness item's action should open, if it maps to one. */
export function editorTabForSection(section: string | null | undefined): EditorTab | null {
  if (!section) return null;
  return SECTION_TAB[section.trim().toLowerCase()] ?? null;
}
