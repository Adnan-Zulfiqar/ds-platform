import type { Metadata } from "next";

import { GlobalRulesWorkspace } from "@/components/global-rules/global-rules-workspace";

export const metadata: Metadata = { title: "Global Rules" };

/**
 * Settings → Global Rules.
 *
 * A Server Component that renders one client workspace. The page itself needs
 * no interactivity — the metadata and the route are its whole job — so the
 * `"use client"` boundary starts one level down, at the first component that
 * actually holds state.
 *
 * Authentication is handled by the `(protected)` layout, which is why there is
 * no guard here: adding a second one would be a second thing to keep correct.
 */
export default function GlobalRulesPage() {
  return <GlobalRulesWorkspace />;
}
