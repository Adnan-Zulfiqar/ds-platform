import type { Page, Route } from "@playwright/test";

import type { EbayListingDefaults, EbayListingSetup, RoleName } from "@/types/api";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, ebayConnection, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * EBAY-C2 listing setup panel — backend-less.
 *
 * The channels fixture answers everything else; this spec layers the three C2
 * endpoints over it (later routes win in Playwright). What it pins down: the
 * panel loads only when opened, saves exactly the chosen ids, says plainly when
 * business policies are off, shows the server's own refusal, and gives members
 * a read-only view.
 */

interface SetupWorld {
  setup: EbayListingSetup;
  saved: EbayListingDefaults[];
  refuseSave: boolean;
  setupReads: number;
}

function baseSetup(overrides: Partial<EbayListingSetup> = {}): EbayListingSetup {
  return {
    marketplaceId: "EBAY_GB",
    supportedMarketplaces: ["EBAY_US", "EBAY_GB", "EBAY_DE"],
    businessPoliciesEnabled: true,
    fulfillmentPolicies: [
      { id: "f-1", name: "Royal Mail 2nd class" },
      { id: "f-2", name: "Free tracked" },
    ],
    paymentPolicies: [{ id: "p-1", name: "Managed payments" }],
    returnPolicies: [{ id: "r-1", name: "30 days, buyer pays" }],
    locations: [
      { key: "wh-1", name: "Leeds unit", city: "Leeds", postalCode: "LS1 1AA", country: "GB", enabled: true },
      { key: "wh-old", name: "Closed shed", city: null, postalCode: null, country: "GB", enabled: false },
    ],
    defaults: null,
    ...overrides,
  };
}

async function openWithSetup(page: Page, setup: EbayListingSetup, role: RoleName = "owner") {
  const world: SetupWorld = { setup, saved: [], refuseSave: false, setupReads: 0 };
  await mockChannelsApi(
    page,
    channelsWorld({
      role,
      ebay: { configured: true, connected: true, connection: ebayConnection({ marketplaceId: "EBAY_GB" }) },
    }),
  );
  await page.route("**/api/v1/integrations/ebay/listing-setup*", async (route: Route) => {
    world.setupReads += 1;
    const marketplaceId = new URL(route.request().url()).searchParams.get("marketplaceId") ?? "EBAY_US";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...world.setup, marketplaceId }),
    });
  });
  await page.route("**/api/v1/integrations/ebay/listing-defaults", async (route: Route) => {
    if (world.refuseSave) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "ebay_policy_not_found",
          message: "That eBay policy or location no longer exists. Reload and choose again.",
          details: [],
          requestId: "req-c2",
        }),
      });
      return;
    }
    const body = route.request().postDataJSON() as Omit<EbayListingDefaults, "updatedAt">;
    const saved = { ...body, updatedAt: new Date().toISOString() };
    world.saved.push(saved);
    world.setup = { ...world.setup, defaults: saved };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(saved) });
  });
  await page.goto("/settings/integrations");
  await expect(page.getByTestId("ebay-card")).toBeVisible({ timeout: 30_000 });
  return world;
}

test.describe("eBay listing setup (EBAY-C2)", () => {
  test("loads only when opened, then saves exactly the chosen ids", async ({ page }) => {
    const world = await openWithSetup(page, baseSetup());
    expect(world.setupReads).toBe(0);

    await page.getByTestId("ebay-listing-setup-toggle").click();
    const panel = page.getByTestId("ebay-listing-setup");
    await expect(panel.getByLabel("Marketplace")).toHaveValue("EBAY_GB");
    await expect(panel.getByLabel("Shipping policy")).toHaveValue("f-1");
    // A disabled location is not offered.
    await expect(panel.getByLabel("Ships from").locator("option")).toHaveCount(1);

    await panel.getByLabel("Shipping policy").selectOption("f-2");
    await panel.getByRole("button", { name: "Save listing defaults" }).click();

    await expect(panel.getByTestId("ebay-defaults-saved")).toBeVisible();
    expect(world.saved).toHaveLength(1);
    expect(world.saved[0]).toMatchObject({
      marketplaceId: "EBAY_GB",
      fulfillmentPolicyId: "f-2",
      paymentPolicyId: "p-1",
      returnPolicyId: "r-1",
      merchantLocationKey: "wh-1",
    });
  });

  test("saved defaults are shown as the current choice", async ({ page }) => {
    await openWithSetup(
      page,
      baseSetup({
        defaults: {
          marketplaceId: "EBAY_GB",
          fulfillmentPolicyId: "f-2",
          paymentPolicyId: "p-1",
          returnPolicyId: "r-1",
          merchantLocationKey: "wh-1",
          updatedAt: new Date().toISOString(),
        },
      }),
    );
    await page.getByTestId("ebay-listing-setup-toggle").click();
    await expect(page.getByTestId("ebay-listing-setup").getByLabel("Shipping policy")).toHaveValue("f-2");
  });

  test("business policies off: an instruction, not empty dropdowns", async ({ page }) => {
    await openWithSetup(
      page,
      baseSetup({
        businessPoliciesEnabled: false,
        fulfillmentPolicies: null,
        paymentPolicies: null,
        returnPolicies: null,
      }),
    );
    await page.getByTestId("ebay-listing-setup-toggle").click();
    const panel = page.getByTestId("ebay-listing-setup");
    await expect(panel.getByTestId("ebay-policies-not-enabled")).toContainText("Seller Hub");
    await expect(panel.getByRole("button", { name: "Save listing defaults" })).toHaveCount(0);
  });

  test("the server's refusal of a stale id is shown, and nothing claims success", async ({ page }) => {
    const world = await openWithSetup(page, baseSetup());
    world.refuseSave = true;
    await page.getByTestId("ebay-listing-setup-toggle").click();
    const panel = page.getByTestId("ebay-listing-setup");
    await panel.getByRole("button", { name: "Save listing defaults" }).click();

    await expect(panel.getByTestId("ebay-listing-defaults-error")).toContainText("no longer exists");
    await expect(panel.getByTestId("ebay-defaults-saved")).toHaveCount(0);
  });

  test("no warehouse yet: the save is blocked and an admin is offered Add", async ({ page }) => {
    await openWithSetup(page, baseSetup({ locations: [] }));
    await page.getByTestId("ebay-listing-setup-toggle").click();
    const panel = page.getByTestId("ebay-listing-setup");
    await expect(panel.getByTestId("ebay-no-locations")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Save listing defaults" })).toBeDisabled();
    await expect(panel.getByRole("button", { name: "Add a warehouse on eBay" })).toBeVisible();
  });

  test("a member sees the setup read-only", async ({ page }) => {
    await openWithSetup(page, baseSetup(), "member");
    await page.getByTestId("ebay-listing-setup-toggle").click();
    const panel = page.getByTestId("ebay-listing-setup");
    await expect(panel.getByLabel("Shipping policy")).toBeDisabled();
    await expect(panel.getByRole("button", { name: "Save listing defaults" })).toHaveCount(0);
    await expect(panel.getByText("Only an owner or admin can change these.")).toBeVisible();
  });

  test("a viewer is not offered the setup at all", async ({ page }) => {
    await openWithSetup(page, baseSetup(), "viewer");
    await expect(page.getByTestId("ebay-listing-setup-toggle")).toHaveCount(0);
  });
});
