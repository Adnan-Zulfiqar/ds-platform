"use client";

import { ChannelStatusBadge } from "@/components/integrations/channel-status-badge";
import {
  deriveAliExpressChannel,
  deriveEbayChannel,
  deriveShopifyChannel,
  type ChannelState,
} from "@/lib/channel-state";
import { useAliExpressStatus, useEbayStatus, useShopifyStatus } from "@/services/integrations";

interface OverviewItem {
  id: string;
  name: string;
  state: ChannelState;
  /** The identity a merchant recognises: store domain, seller name. */
  identity: string | null;
}

/**
 * The page's answer to "which channels are connected?" before any card is
 * read. Reads the same three status queries the cards read — one request
 * each, deduplicated by React Query — and links each row to its card.
 */
export function ChannelOverview() {
  const shopify = deriveShopifyChannel(useShopifyStatus());
  const aliexpress = useAliExpressStatus();
  const ebay = useEbayStatus();
  const aliexpressState = deriveAliExpressChannel(aliexpress);
  const ebayState = deriveEbayChannel(ebay);

  const connectedShops = shopify.connections.filter((c) => c.kind === "connected");
  const items: OverviewItem[] = [
    {
      id: "aliexpress",
      name: "AliExpress",
      state: aliexpressState,
      identity: null,
    },
    {
      id: "shopify",
      name: "Shopify",
      state: shopify,
      identity:
        shopify.connections.length === 1
          ? shopify.connections[0]!.connection.shopDomain
          : shopify.connections.length > 1
            ? `${connectedShops.length} of ${shopify.connections.length} stores connected`
            : null,
    },
    {
      id: "ebay",
      name: "eBay",
      state: ebayState,
      identity: ebay.data?.connection?.ebayUsername ?? null,
    },
  ];

  return (
    <nav aria-label="Channel overview" data-testid="channel-overview">
      {/* Two columns from `sm`, three from `xl`: at 1024 with the sidebar open
          a third column is too narrow for a name beside "1 needs webhook
          setup", and the badge wraps under the name rather than over it. */}
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <li key={item.id} className="min-w-0">
            <a
              href={`#channel-${item.id}`}
              className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 overflow-hidden rounded-md border bg-card px-3 py-2 text-sm hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              data-testid={`overview-${item.id}`}
            >
              <span className="min-w-[7rem] flex-1">
                <span className="block font-medium">{item.name}</span>
                {item.identity ? (
                  <span className="block truncate text-xs text-muted-foreground">{item.identity}</span>
                ) : null}
              </span>
              <ChannelStatusBadge state={item.state} />
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
