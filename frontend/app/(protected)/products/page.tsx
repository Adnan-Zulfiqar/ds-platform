import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Products" };

/**
 * The route exists so the sidebar link resolves. The feature does not.
 *
 * Product import and catalogue management belong to a later phase; this page is
 * honest about that rather than showing an empty table that implies the feature
 * works and the user simply has no data.
 */
export default function ProductsPage() {
  return (
    <ComingSoon
      title="Products"
      description="Manage your product catalogue across every connected channel."
      planned={[
        "Import products from AliExpress and other suppliers",
        "Bulk edit titles, descriptions, and pricing",
        "AI-assisted listing optimisation",
        "Variant and inventory management",
        "Publish to connected stores",
      ]}
    />
  );
}
