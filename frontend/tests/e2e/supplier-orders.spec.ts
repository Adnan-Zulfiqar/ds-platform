import type { Page, Route } from "@playwright/test";

import type { OrderDetail, RoleName } from "@/types/api";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * Track F screens, backend-less: the supplier-order panel on a Shopify
 * order and the Fulfilment settings page. Pins down: review reasons read as
 * sentences, "Place on AliExpress" posts once and the panel follows the
 * queued → placed state, a placed order links to payment on AliExpress, the
 * tracking push is offered only with one parcel, and viewers see no actions.
 */

const ORDER_ID = "77777777-7777-4777-8777-777777777777";

function shopifyOrder(): OrderDetail {
  const now = new Date().toISOString();
  return {
    id: ORDER_ID,
    source: "shopify",
    externalId: "5551",
    externalStatus: "paid",
    fulfillmentStatus: "paid",
    paymentStatus: "paid",
    buyerName: "Jane Buyer",
    countryCode: "GB",
    currency: "USD",
    totalAmount: "20.00",
    itemCount: 1,
    externalCreatedAt: now,
    lastSyncedAt: now,
    lastSyncError: null,
    createdAt: now,
    buyerCountry: "GB",
    recipientName: "Jane Buyer",
    recipientPhone: "+44 7700 900000",
    addressLine1: "1 High Street",
    addressLine2: null,
    city: "London",
    province: null,
    postalCode: "N1 1AA",
    shippingAmount: "0.00",
    paidAt: now,
    deliveredAt: null,
    items: [],
    shipments: [],
  } as OrderDetail;
}

function supplier(overrides: Record<string, unknown> = {}) {
  return {
    status: "none",
    trigger: null,
    reviewReasons: [],
    externalOrderIds: [],
    errorCode: null,
    errorMessage: null,
    placedAt: null,
    trackingNumber: null,
    trackingCarrier: null,
    trackingPushedAt: null,
    paymentUrl: null,
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockOrder(page: Page, role: RoleName = "owner") {
  await mockChannelsApi(page, channelsWorld({ role }));
  await page.route(`**/api/v1/orders/${ORDER_ID}`, (route) => json(route, shopifyOrder()));
  await page.route(`**/api/v1/orders/${ORDER_ID}/timeline`, (route) => json(route, []));
}

test("a clean order is placed once and the panel follows it to payment", async ({ page }) => {
  await mockOrder(page);
  let state = supplier();
  const posts: string[] = [];
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order`, (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().url());
      state = supplier({ status: "queued", trigger: "manual" });
      return json(route, state, 202);
    }
    // The task finishes: the next poll after "queued" sees the placed order.
    const answer = state;
    if (state.status === "queued") {
      state = supplier({
        status: "placed",
        externalOrderIds: ["8001"],
        placedAt: new Date().toISOString(),
        paymentUrl: "https://www.aliexpress.com/p/order/index.html",
      });
    }
    return json(route, answer);
  });

  await page.goto(`/orders/${ORDER_ID}`);
  const panel = page.getByTestId("supplier-order-panel");
  await expect(page.getByTestId("supplier-order-status")).toHaveText("Not sent to AliExpress");

  await panel.getByRole("button", { name: "Place on AliExpress" }).click();
  await expect(page.getByTestId("supplier-order-status")).toHaveText(
    "Placed, awaiting payment on AliExpress",
    { timeout: 10_000 },
  );
  await expect(panel).toContainText("8001");
  await expect(panel.getByRole("link", { name: /Pay on AliExpress/ })).toHaveAttribute(
    "href",
    "https://www.aliexpress.com/p/order/index.html",
  );
  await expect(panel.getByRole("button", { name: "Place on AliExpress" })).toHaveCount(0);
  expect(posts).toHaveLength(1);
});

test("review reasons read as sentences and the order can be retried", async ({ page }) => {
  await mockOrder(page);
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order`, (route) =>
    json(
      route,
      supplier({
        status: "needs_review",
        reviewReasons: ["missing_recipient_phone", "line_2:variant_unknown", "tax_id_required"],
      }),
    ),
  );

  await page.goto(`/orders/${ORDER_ID}`);
  const reasons = page.getByTestId("supplier-order-reasons");
  await expect(reasons).toContainText("The shipping address has no phone number");
  await expect(reasons).toContainText("Line 2: the store did not say which variant was sold.");
  await expect(reasons).toContainText("place it on AliExpress by hand");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("tracking can be sent to the store for a single parcel", async ({ page }) => {
  await mockOrder(page);
  let pushed = 0;
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order`, (route) =>
    json(
      route,
      supplier({
        status: "placed",
        externalOrderIds: ["8001"],
        trackingNumber: "LP00123",
        trackingCarrier: "Cainiao",
        paymentUrl: "https://www.aliexpress.com/p/order/index.html",
      }),
    ),
  );
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order/push-tracking`, (route) => {
    pushed += 1;
    return json(
      route,
      supplier({
        status: "shipped",
        externalOrderIds: ["8001"],
        trackingNumber: "LP00123",
        trackingCarrier: "Cainiao",
        trackingPushedAt: new Date().toISOString(),
      }),
    );
  });

  await page.goto(`/orders/${ORDER_ID}`);
  await expect(page.getByTestId("supplier-order-panel")).toContainText("LP00123 (Cainiao)");
  await page.getByRole("button", { name: "Send tracking to the store" }).click();
  await expect(page.getByTestId("supplier-order-status")).toHaveText("Tracking sent to the store");
  expect(pushed).toBe(1);
});

test("a viewer sees the supplier order but no actions", async ({ page }) => {
  await mockOrder(page, "viewer");
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order`, (route) =>
    json(route, supplier()),
  );
  await page.goto(`/orders/${ORDER_ID}`);
  await expect(page.getByTestId("supplier-order-status")).toHaveText("Not sent to AliExpress");
  await expect(page.getByRole("button", { name: "Place on AliExpress" })).toHaveCount(0);
});

test("the fulfilment switches start off and save exactly what was chosen", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const saved: unknown[] = [];
  await page.route("**/api/v1/orders/fulfilment/settings", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      saved.push(body);
      return json(route, body);
    }
    return json(route, { autoOrder: false, autoTracking: false, fallbackShippingMethod: null });
  });

  await page.goto("/settings");
  await expect(page.getByRole("link", { name: /Fulfilment/ })).toHaveAttribute(
    "href",
    "/settings/fulfilment",
  );
  await page.goto("/settings/fulfilment");
  const order = page.getByLabel(/Place paid orders on AliExpress automatically/);
  const tracking = page.getByLabel(/Send AliExpress tracking to the store automatically/);
  await expect(order).not.toBeChecked();
  await expect(tracking).not.toBeChecked();
  const saveButton = page.getByRole("button", { name: "Save" });
  await expect(saveButton).toBeDisabled();

  await order.check();
  await tracking.check();
  await page.getByLabel("Fallback shipping method (optional)").fill("  CAINIAO_STANDARD ");
  await saveButton.click();

  await expect(page.getByRole("status")).toHaveText("Saved.");
  expect(saved).toEqual([
    { autoOrder: true, autoTracking: true, fallbackShippingMethod: "CAINIAO_STANDARD" },
  ]);
});

test("an unclear answer asks the merchant to check AliExpress, then release", async ({
  page,
}) => {
  await mockOrder(page);
  let state = supplier({
    status: "placing",
    errorCode: "outcome_unknown",
    errorMessage:
      "AliExpress did not answer clearly (aliexpress_timeout). Check your AliExpress orders before trying again, so the goods are not bought twice.",
  });
  let released = 0;
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order`, (route) => json(route, state));
  await page.route(`**/api/v1/orders/${ORDER_ID}/supplier-order/release`, (route) => {
    released += 1;
    state = supplier({
      status: "failed",
      errorCode: "released_by_merchant",
      errorMessage: "Released after checking AliExpress: no order had been created.",
    });
    return json(route, state);
  });

  await page.goto(`/orders/${ORDER_ID}`);
  const panel = page.getByTestId("supplier-order-panel");
  await expect(page.getByTestId("supplier-order-unknown")).toContainText(
    "Check your AliExpress orders",
  );
  // No "Try again" while the outcome is unknown: that could buy twice.
  await expect(panel.getByRole("button", { name: /Try again|Place on AliExpress/ })).toHaveCount(0);

  await panel.getByRole("button", { name: /No order on AliExpress\? Release/ }).click();
  expect(released).toBe(0); // the first click only arms
  await panel.getByRole("button", { name: /Confirm: AliExpress has no such order/ }).click();

  await expect(page.getByTestId("supplier-order-status")).toHaveText("Not placed on AliExpress");
  await expect(panel.getByRole("button", { name: "Try again" })).toBeVisible();
  expect(released).toBe(1);
});
