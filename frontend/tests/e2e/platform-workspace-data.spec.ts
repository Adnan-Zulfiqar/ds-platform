import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  TENANT_ID,
  confirmReauth,
  goTo,
  mockPlatform,
  page1,
  signIn,
} from "./platform-mock";

/**
 * Admin Control Center phase 3 (D-019): an operator drills into one
 * workspace. Pins down:
 * - each tab calls that workspace's own endpoint with its filters;
 * - order detail shows buyer and supplier data;
 * - an export asks for re-authentication and then downloads a CSV.
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

async function openWorkspace(page: import("@playwright/test").Page) {
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await expect(page.getByTestId("platform-workspace-overview")).toBeVisible();
}

test("users, stores and connections render from the workspace's endpoints", async ({
  page,
}) => {
  const log = await mockPlatform(page, {
    handlers: {
      [`GET ${W}/users`]: () =>
        page1([
          {
            id: "u1",
            email: "owner@acme.test",
            firstName: "Ada",
            lastName: "Owner",
            isActive: true,
            isVerified: true,
            lastLoginAt: NOW,
            createdAt: NOW,
            roles: ["owner"],
            activeSessions: 2,
          },
        ]),
      [`GET ${W}/invitations`]: () => page1([]),
      [`GET ${W}/stores`]: () =>
        page1([
          {
            id: "s1",
            name: "Acme Shopify",
            platform: "shopify",
            status: "connected",
            currency: "USD",
            inventorySyncEnabled: true,
            pricingSyncEnabled: false,
            orderSyncEnabled: true,
            lastSyncAt: NOW,
            lastError: null,
            healthScore: 92,
          },
        ]),
      [`GET ${W}/connections`]: () => [
        {
          kind: "aliexpress",
          id: "c1",
          status: "connected",
          label: null,
          storeId: null,
          lastSyncAt: NOW,
          tokenExpiresAt: NOW,
          webhooksRegisteredAt: null,
          lastError: null,
          createdAt: NOW,
        },
      ],
    },
  });
  await openWorkspace(page);

  await page.getByRole("button", { name: "Users" }).click();
  await expect(page.getByTestId("platform-workspace-users")).toContainText(
    "owner@acme.test",
  );
  await expect(page.getByTestId("platform-workspace-users")).toContainText(
    "owner",
  );

  await page.getByRole("button", { name: "Stores" }).click();
  await expect(page.getByTestId("platform-workspace-stores")).toContainText(
    "orders, inventory",
  );
  await expect(
    page.getByTestId("platform-workspace-connections"),
  ).toContainText("aliexpress");
  await page.getByLabel("Status").selectOption("error");
  await expect
    .poll(() => log.some((e) => e.path === `${W}/stores` && e.method === "GET"))
    .toBe(true);
});

test("an order opens with buyer, items and supplier order", async ({
  page,
}) => {
  await mockPlatform(page, {
    handlers: {
      [`GET ${W}/orders`]: () => page1([ORDER]),
      [`GET ${W}/orders/o1`]: () => ({
        ...ORDER,
        recipientName: "Jane Buyer",
        recipientPhone: "+44 1234",
        addressLine1: "1 High St",
        addressLine2: null,
        city: "Ilford",
        province: null,
        postalCode: "IG1",
        countryCode: "GB",
        shippingAmount: "3.00",
        paidAt: NOW,
        deliveredAt: null,
        items: [
          {
            id: "i1",
            title: "Garden lamp",
            skuAttributes: "Black",
            quantity: 2,
            unitPrice: "11.00",
            currency: "GBP",
            productId: null,
          },
        ],
        shipments: [],
        events: [],
        supplierOrders: [
          {
            id: "so1",
            status: "placed",
            trigger: "manual",
            reviewReasons: [],
            externalOrderIds: ["AE-77"],
            errorCode: null,
            errorMessage: null,
            placedAt: NOW,
            trackingNumber: null,
            trackingCarrier: null,
            trackingPushedAt: null,
            createdAt: NOW,
          },
        ],
      }),
    },
  });
  await openWorkspace(page);
  await page.getByRole("button", { name: "Orders" }).click();
  await page
    .getByTestId("platform-workspace-orders")
    .getByText("#1001")
    .click();

  const detail = page.getByTestId("platform-order-detail");
  await expect(detail).toContainText("1 High St, Ilford, IG1, GB");
  await expect(detail).toContainText("2 × Garden lamp");
  await expect(detail).toContainText("AE-77");
});

test("an export asks for re-authentication, then downloads the CSV", async ({
  page,
}) => {
  let reauthed = false;
  await mockPlatform(page, {
    handlers: {
      "POST /auth/reauth": () => {
        reauthed = true;
        return { reauthenticatedAt: NOW, validUntil: NOW };
      },
    },
  });
  await page.route(`**/api/v1/platform${W}/export/orders`, (route) =>
    reauthed
      ? route.fulfill({
          status: 200,
          contentType: "text/csv",
          headers: {
            "Content-Disposition": 'attachment; filename="acme-orders.csv"',
          },
          body: "id,buyer_name\no1,Jane\n",
        })
      : route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({
            code: "reauth_required",
            message: "Confirm",
            details: [],
            requestId: "r",
          }),
        }),
  );
  await openWorkspace(page);
  await page.getByRole("button", { name: "Export" }).click();

  const download = page.waitForEvent("download");
  await page
    .getByTestId("platform-workspace-export")
    .getByRole("button", { name: "Orders" })
    .click();
  await confirmReauth(page);
  expect((await download).suggestedFilename()).toBe("acme-orders.csv");
});
