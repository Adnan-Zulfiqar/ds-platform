import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  TENANT_ID,
  goTo,
  mockPlatform,
  page1,
  signIn,
} from "./platform-mock";

/**
 * Admin Control Center phase 6 (D-019). Pins down:
 * - release is offered only for a supplier order stuck in `placing`;
 * - it needs a second click to confirm and sends exactly the reason.
 */

const W = `/workspaces/${TENANT_ID}`;
const ORDER = {
  id: "o1",
  source: "shopify",
  externalId: "#1001",
  storeId: null,
  fulfillmentStatus: "paid",
  paymentStatus: "paid",
  buyerName: "Jane Buyer",
  buyerCountry: "GB",
  currency: "GBP",
  totalAmount: "25.00",
  externalCreatedAt: NOW,
  lastSyncedAt: NOW,
  lastSyncError: null,
  createdAt: NOW,
};
const DETAIL = {
  ...ORDER,
  recipientName: null,
  recipientPhone: null,
  addressLine1: null,
  addressLine2: null,
  city: null,
  province: null,
  postalCode: null,
  countryCode: null,
  shippingAmount: null,
  paidAt: null,
  deliveredAt: null,
  items: [],
  shipments: [],
  events: [],
  supplierOrders: [
    {
      id: "so1",
      status: "placing",
      trigger: "manual",
      reviewReasons: [],
      externalOrderIds: [],
      errorCode: "outcome_unknown",
      errorMessage: "No answer",
      placedAt: null,
      trackingNumber: null,
      trackingCarrier: null,
      trackingPushedAt: null,
      createdAt: NOW,
    },
  ],
};

test("a stuck supplier order is released with a confirmed reason", async ({
  page,
}) => {
  const log = await mockPlatform(page, {
    handlers: {
      [`GET ${W}/orders`]: () => page1([ORDER]),
      [`GET ${W}/orders/o1`]: () => DETAIL,
      [`POST ${W}/orders/o1/supplier-order/release`]: () => ({
        ...DETAIL.supplierOrders[0],
        status: "failed",
        errorCode: "released_by_support",
      }),
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await page.getByRole("button", { name: "Orders" }).click();
  await page
    .getByTestId("platform-workspace-orders")
    .getByText("#1001")
    .click();

  const actions = page.getByTestId("platform-order-actions");
  await actions
    .getByLabel("Reason for Release stuck supplier order")
    .fill("checked AliExpress: no order");
  await actions
    .getByRole("button", { name: "Release stuck supplier order" })
    .click();
  await actions
    .getByRole("button", { name: "Confirm: release stuck supplier order" })
    .click();
  await expect(actions).toContainText("Done. Recorded in the audit log.");
  expect(
    log.filter((e) => e.path === `${W}/orders/o1/supplier-order/release`).pop()
      ?.body,
  ).toEqual({ reason: "checked AliExpress: no order" });
});
