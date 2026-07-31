import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Connected Stores" };

export default function StoresPage() {
  return (
    <ComingSoon
      title="Connected Stores"
      description="Connect and manage the sales channels you sell through."
      planned={[
        "Connect Shopify, WooCommerce, eBay, Etsy, and TikTok Shop",
        "OAuth-based authorisation per channel",
        "Per-store sync status and error reporting",
        "Channel-specific pricing and listing rules",
      ]}
    />
  );
}
