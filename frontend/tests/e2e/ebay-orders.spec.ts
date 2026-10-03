import type { Page, Route } from "@playwright/test";

import type { OrderDetail, OrderStatistics, RoleName } from "@/types/api";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, ebayConnection, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * EBAY-C5 on the Orders pages — backend-less.
 *
 * The channels fixture answers auth and eBay status (connected); this spec
 * answers the order endpoints and the two eBay order actions. Pins down: the
 * import button appears only for a connected eBay and an admin, and the ship
 * form sends exactly the carrier and tracking number.
 */

const ORDER_ID = "88888888-8888-4888-8888-888888888888";

function ebayOrderDetail(overrides: Partial<OrderDetail> = {}): OrderDetail {
  const now = new Date().toISOString();
  return {
    id: ORDER_ID,
    source: "ebay",
    externalId: "12-34567-89012",
    externalStatus: "NOT_STARTED",
    fulfillmentStatus: "paid",
    paymentStatus: "paid",
    buyerName: "Jane Buyer",
    countryCode: "US",
    currency: "USD",
    totalAmount: "17.49",
    itemCount: 1,
    externalCreatedAt: now,
    lastSyncedAt: now,
    lastSyncError: null,
    createdAt: now,
    buyerCountry: "US",
    recipientName: "Jane Buyer",
    recipientPhone: null,
    addressLine1: "1 Main St",
    addressLine2: null,
    city: "Austin",
    province: "TX",
    postalCode: "78701",
    shippingAmount: "4.99",
    paidAt: now,
    deliveredAt: null,
    items: [
      {
        id: "99999999-9999-4999-8999-999999999999",
        externalItemId: "12-34567-89012-L1",
        productId: null,
        externalProductId: "110000000001",
        title: "Red ceramic mug",
        skuAttributes: null,
        quantity: 1,
        unitPrice: "12.50",
        currency: "USD",
        externalStatus: "NOT_STARTED",
      },
    ],
    shipments: [],
    ...overrides,
  } as OrderDetail;
}

const STATS: OrderStatistics = {
  totalOrders: 1,
  byStatus: { paid: 1 },
  pendingFulfillment: 1,
  processing: 0,
  delivered: 0,
  failedSyncsLast7Days: 0,
  lastSync: null,
  webhookEventsReceived: null,
};

async function mockOrders(page: Page, role: RoleName = "owner") {
  const calls = { imports: 0, shipments: [] as unknown[] };
  await mockChannelsApi(
    page,
    channelsWorld({ role, ebay: { configured: true, connected: true, connection: ebayConnection() } }),
  );
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/v1/orders/statistics", (route) => json(route, STATS));
  await page.route(/\/api\/v1\/orders(\?.*)?$/, (route) =>
    json(route, {
      items: [ebayOrderDetail()],
      meta: { page: 1, size: 20, totalItems: 1, totalPages: 1, hasNext: false, hasPrevious: false },
    }),
  );
  await page.route(`**/api/v1/orders/${ORDER_ID}`, (route) => json(route, ebayOrderDetail()));
  await page.route(`**/api/v1/orders/${ORDER_ID}/timeline`, (route) => json(route, []));
  await page.route("**/api/v1/integrations/ebay/orders/import*", (route) => {
    calls.imports += 1;
    return json(route, { fetched: 2, created: 1, updated: 1 });
  });
  await page.route(`**/api/v1/integrations/ebay/orders/${ORDER_ID}/shipments`, (route) => {
    calls.shipments.push(route.request().postDataJSON());
    return json(route, { id: "s-1", orderId: ORDER_ID }, 201);
  });
  return calls;
}

test.describe("eBay orders (EBAY-C5)", () => {
  test("an admin imports eBay orders from the Orders page", async ({ page }) => {
    const calls = await mockOrders(page);
    await page.goto("/orders");
    await page.getByTestId("ebay-import-orders").click();
    await expect(page.getByTestId("ebay-import-result")).toHaveText("1 new, 1 updated from eBay.");
    expect(calls.imports).toBe(1);
  });

  test("a viewer is not offered the import", async ({ page }) => {
    await mockOrders(page, "viewer");
    await page.goto("/orders");
    await expect(page.getByRole("heading", { name: "Orders" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("ebay-import-orders")).toHaveCount(0);
  });

  test("marking shipped sends exactly the carrier and tracking number", async ({ page }) => {
    const calls = await mockOrders(page);
    await page.goto(`/orders/${ORDER_ID}`);
    const form = page.getByTestId("ebay-ship-form");
    await expect(form).toBeVisible({ timeout: 30_000 });

    await form.getByLabel("Carrier").selectOption("UPS");
    await form.getByLabel("Tracking number").fill("1Z999AA10123456784");
    await form.getByRole("button", { name: "Send to eBay" }).click();

    await expect(form.getByTestId("ebay-ship-result")).toBeVisible();
    expect(calls.shipments).toEqual([{ carrierCode: "UPS", trackingNumber: "1Z999AA10123456784" }]);
  });
});
