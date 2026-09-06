/**
 * UX-L2B-R4 — store status guidance must follow workspace store state, not a missing listing alone.
 */
import { expect, test } from "@playwright/test";

import { deriveStoreStatusLabel } from "@/components/drafts/editor-header/store-status-label";
import type { Store } from "@/services/stores";
import type { StoreListing } from "@/types/api";

const connectedStore = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Demo Shopify",
  slug: "demo",
  platform: "shopify",
  status: "connected",
} as Store;

const disconnectedStore = {
  ...connectedStore,
  status: "disconnected",
} as Store;

const syncedListing = {
  id: "22222222-2222-4222-8222-222222222222",
  storeId: connectedStore.id,
  status: "synced",
  shopDomain: "demo.myshopify.com",
} as StoreListing;

test.describe("deriveStoreStatusLabel", () => {
  test("loading never claims Connect Shopify", () => {
    expect(
      deriveStoreStatusLabel({
        storesPending: true,
        storesError: false,
        shopifyStores: [],
        listing: null,
      }),
    ).toBe("Choose a store in Review & publish");
  });

  test("unavailable uses neutral wording", () => {
    expect(
      deriveStoreStatusLabel({
        storesPending: false,
        storesError: true,
        shopifyStores: [],
        listing: null,
      }),
    ).toBe("Store status unavailable");
  });

  test("no usable Shopify store asks to connect", () => {
    expect(
      deriveStoreStatusLabel({
        storesPending: false,
        storesError: false,
        shopifyStores: [disconnectedStore],
        listing: null,
      }),
    ).toBe("Connect Shopify to publish");
  });

  test("connected store without listing asks to choose a store", () => {
    expect(
      deriveStoreStatusLabel({
        storesPending: false,
        storesError: false,
        shopifyStores: [connectedStore],
        listing: null,
      }),
    ).toBe("Choose a store in Review & publish");
  });

  test("synced listing shows shop domain", () => {
    expect(
      deriveStoreStatusLabel({
        storesPending: false,
        storesError: false,
        shopifyStores: [connectedStore],
        listing: syncedListing,
      }),
    ).toBe("demo.myshopify.com");
  });
});
