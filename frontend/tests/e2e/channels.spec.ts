import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import {
  aliExpressConnection,
  channelsWorld,
  ebayConnection,
  mockChannelsApi,
  SHOP_STORE_ID,
  shopifyConnection,
  storeRecord,
  type ChannelsWorld,
} from "./helpers/channels-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";

/**
 * Channels / Integrations (UX-L2D-06) — backend-less.
 *
 * Every state a provider card can show is reached through a payload the API
 * can actually return; connect, disconnect and webhook retry are exercised
 * against the mocked endpoints with consent pages stubbed so nothing leaves
 * the test. The backend-gated `integrations`, `shopify-oauth`,
 * `shopify-webhook-recovery` and `ebay` specs keep their live coverage.
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-06-channels", ["UX_L2D_06_SHOT_ROOT"]);

async function shot(page: Page, name: string) {
  await captureEvidenceScreenshot(page, name, { root: SHOT_ROOT, fullPage: false });
}

async function openIntegrations(page: Page, world: ChannelsWorld = channelsWorld()) {
  const log = await mockChannelsApi(page, world);
  await page.goto("/settings/integrations");
  await expect(page.getByTestId("channel-shopify-status")).toBeVisible({ timeout: 30_000 });
  return { log, world };
}

function suppliers(page: Page) {
  return page.getByRole("region", { name: "Suppliers" });
}
function channels(page: Page) {
  return page.getByRole("region", { name: "Sales channels" });
}
function shopifyCard(page: Page) {
  return page.getByTestId("channel-shopify");
}
function aliexpressCard(page: Page) {
  return page.getByTestId("channel-aliexpress");
}
function ebayCard(page: Page) {
  return page.getByTestId("ebay-card");
}

async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    const main = document.getElementById("main-content");
    return doc.scrollWidth > doc.clientWidth + 1 || (main !== null && main.scrollWidth > main.clientWidth + 1);
  });
  expect(overflow, "page must not scroll horizontally").toBe(false);
}

const SECRET_PATTERNS = [
  /access.?token/i,
  /refresh.?token/i,
  /app.?secret/i,
  /client.?secret/i,
  /ALIEXPRESS_APP/,
  /SHOPIFY_API/,
  /"errors"/,
  /HTTP 401/,
];

async function assertNoSecretsOrRawErrors(page: Page) {
  const text = await page.locator("#main-content").innerText();
  for (const pattern of SECRET_PATTERNS) {
    expect(text, `page text must not match ${pattern}`).not.toMatch(pattern);
  }
}

test.describe("Channels — Shopify", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("not connected: one Connect Shopify that asks only for a domain and hands off to Shopify", async ({ page }) => {
    const { log } = await openIntegrations(page);
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Not connected");
    await expect(page.getByTestId("overview-shopify")).toContainText("Not connected");
    await shot(page, "1440-light-not-connected");

    await channels(page).getByRole("button", { name: "Connect Shopify" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Store domain")).toBeVisible();
    for (const label of ["API key", "API secret", "Access token", "Password"]) {
      await expect(dialog.getByLabel(label)).toHaveCount(0);
    }
    await dialog.getByLabel("Store domain").fill("demo-shop");
    await dialog.getByRole("button", { name: "Continue to Shopify" }).click();
    await page.waitForURL(/demo-shop\.myshopify\.com\/admin\/oauth\/authorize/, { timeout: 15_000 });
    expect(log.requests.filter((r) => r.method === "POST" && r.path === "/integrations/shopify/connect")).toHaveLength(1);
    // The manual store-creation endpoint is never touched by any merchant path.
    expect(log.requests.filter((r) => r.method === "POST" && r.path === "/stores")).toHaveLength(0);
  });

  test("connected: identity, webhook confirmation and a disconnect that explains itself", async ({ page }) => {
    const { log, world } = await openIntegrations(
      page,
      channelsWorld({
        shopify: { configured: true, connections: [shopifyConnection()] },
        stores: [storeRecord()],
      }),
    );
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("1 connected");
    await expect(card.getByText("demo-shop.myshopify.com")).toBeVisible();
    await expect(card.getByText("Webhooks last confirmed", { exact: true })).toBeVisible();
    await expect(card.getByText(/Webhooks last confirmed:/)).toBeVisible();
    await expect(page.getByTestId("overview-shopify")).toContainText("demo-shop.myshopify.com");
    await shot(page, "1440-light-shopify-connected");

    await card.getByRole("button", { name: "Disconnect" }).click();
    const dialog = page.getByTestId("disconnect-dialog-shopify");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/nothing is deleted from your store/i);
    await expect(dialog).toContainText(/products and listings recorded in DropPilot are kept/i);
    await shot(page, "1440-light-shopify-disconnect-dialog");
    await dialog.getByRole("button", { name: "Keep connected" }).click();
    await expect(dialog).toHaveCount(0);
    expect(log.requests.filter((r) => r.method === "DELETE")).toHaveLength(0);

    await card.getByRole("button", { name: "Disconnect" }).click();
    await page.getByTestId("disconnect-confirm-shopify").click();
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Not connected", { timeout: 10_000 });
    expect(log.requests.filter((r) => r.method === "DELETE" && r.path === `/integrations/shopify/stores/${SHOP_STORE_ID}`)).toHaveLength(1);
    expect(world.stores[0]?.status).toBe("disconnected");
  });

  test("webhook-degraded: needs attention, retry available, no retry on render", async ({ page }) => {
    const { log } = await openIntegrations(
      page,
      channelsWorld({
        shopify: {
          configured: true,
          connections: [shopifyConnection({ webhooksRegisteredAt: null, webhookHealth: "degraded" })],
        },
      }),
    );
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("1 needs webhook setup");
    await expect(card.getByText("Webhooks incomplete")).toBeVisible();
    await expect(card.getByText("Webhook setup for this store did not complete.")).toBeVisible();
    // The consequence is stated exactly once — in the warning alert, not
    // again in the row detail (a strict locator must resolve to one element).
    const consequence = card.getByText(/Product, inventory and order updates may be missed/i);
    await expect(consequence).toHaveCount(1);
    await expect(consequence).toBeVisible();
    await expect(card.getByText(/Webhooks last confirmed:/)).toHaveCount(0);
    expect(log.requests.filter((r) => r.path.endsWith("/webhooks/reconcile"))).toHaveLength(0);
    await shot(page, "1440-light-shopify-webhooks-degraded");

    await card.getByRole("button", { name: "Retry webhook setup" }).click();
    const status = page.getByTestId(`shopify-webhook-status-${SHOP_STORE_ID}`);
    await expect(status).toContainText(/Webhook setup confirmed/i, { timeout: 10_000 });
    await expect(status).toBeFocused();
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("1 connected");
    expect(log.requests.filter((r) => r.path.endsWith("/webhooks/reconcile"))).toHaveLength(1);
  });

  test("a store in error never shows the provider's raw text", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({
        shopify: {
          configured: true,
          connections: [shopifyConnection({ status: "error", lastError: 'HTTP 401 {"errors":"[API] Invalid API key or access token"}' })],
        },
      }),
    );
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Needs attention");
    await expect(card.getByTestId(`shopify-connection-status-${SHOP_STORE_ID}`)).toHaveText("Needs attention");
    await expect(card.getByRole("button", { name: "Reconnect" })).toBeVisible();
    await assertNoSecretsOrRawErrors(page);
    await shot(page, "1440-light-shopify-error");
  });

  test("an expired authorization is Reconnect required with Reconnect as the primary action", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({ shopify: { configured: true, connections: [shopifyConnection({ status: "expired" })] } }),
    );
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Reconnect required");
    await card.getByRole("button", { name: "Reconnect" }).click();
    await page.waitForURL(/demo-shop\.myshopify\.com\/admin\/oauth\/authorize/, { timeout: 15_000 });
  });

  test("no app credentials on the server: Setup unavailable, no dead Connect, no env names", async ({ page }) => {
    await openIntegrations(page, channelsWorld({ shopify: { configured: false, connections: [] } }));
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Setup unavailable");
    await expect(card.getByText(/ask your DropPilot operator/i)).toBeVisible();
    await expect(card.getByRole("button", { name: "Connect Shopify" })).toBeDisabled();
    await assertNoSecretsOrRawErrors(page);
    await shot(page, "1440-light-shopify-setup-unavailable");
  });

  test("a failed status request is Status unavailable with a retry — never Not connected", async ({ page }) => {
    const world = channelsWorld({ shopify: { configured: true, connections: [shopifyConnection()] } });
    world.fail.add("shopify");
    await mockChannelsApi(page, world);
    await page.goto("/settings/integrations");
    const card = shopifyCard(page);
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("Status unavailable", { timeout: 30_000 });
    await expect(card.getByText("Not connected")).toHaveCount(0);
    await shot(page, "1440-light-shopify-status-unavailable");
    world.fail.delete("shopify");
    await card.getByRole("button", { name: "Try again" }).click();
    await expect(card.getByTestId("channel-shopify-status")).toHaveText("1 connected", { timeout: 10_000 });
  });

  test("a viewer sees the state and a read-only note, with no way to connect or disconnect", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({ role: "viewer", shopify: { configured: true, connections: [shopifyConnection()] } }),
    );
    const card = shopifyCard(page);
    await expect(card.getByText("Read only")).toBeVisible();
    await expect(card.getByText(/Ask an administrator/i)).toBeVisible();
    await expect(card.getByRole("button", { name: /Disconnect|Connect Shopify|Connect another store/ })).toHaveCount(0);
  });
});

test.describe("Channels — AliExpress", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("not connected → Connect hands off; a pending row offers Continue and Disconnect", async ({ page }) => {
    const { world } = await openIntegrations(page);
    const card = aliexpressCard(page);
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Not connected");
    await expect(suppliers(page).getByLabel("App key")).toHaveCount(0);
    await expect(suppliers(page).getByLabel("App secret")).toHaveCount(0);
    await suppliers(page).getByRole("button", { name: "Connect AliExpress" }).click();
    await page.waitForURL(/auth\.aliexpress\.com/, { timeout: 15_000 });

    // The fixture moved the row to pending, as the backend does.
    expect(world.aliexpress.connection?.status).toBe("pending");
    await page.goto("/settings/integrations");
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Awaiting authorization", { timeout: 30_000 });
    await expect(card.getByRole("button", { name: "Continue on AliExpress" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Disconnect" })).toBeVisible();
    await shot(page, "1440-light-aliexpress-pending");
  });

  test("connected: facts and a confirmed disconnect returns to Not connected", async ({ page }) => {
    const { log } = await openIntegrations(
      page,
      channelsWorld({ aliexpress: { connected: true, connection: aliExpressConnection() } }),
    );
    const card = aliexpressCard(page);
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Connected");
    await expect(card.getByText("Last sync")).toBeVisible();
    await card.getByRole("button", { name: "Disconnect" }).click();
    const dialog = page.getByTestId("disconnect-dialog-aliexpress");
    await expect(dialog).toContainText(/drafts already in DropPilot are kept/i);
    await page.getByTestId("disconnect-confirm-aliexpress").click();
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Not connected", { timeout: 10_000 });
    expect(log.requests.filter((r) => r.method === "DELETE" && r.path === "/integrations/aliexpress/disconnect")).toHaveLength(1);
  });

  test("an expired token is Reconnect required, with the backend's curated message", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({
        aliexpress: {
          connected: false,
          connection: aliExpressConnection({
            status: "expired",
            isTokenExpired: true,
            lastError: "AliExpress rejected the credentials for this connection.",
          }),
        },
      }),
    );
    const card = aliexpressCard(page);
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Reconnect required");
    await expect(card.getByText("AliExpress rejected the credentials for this connection.")).toBeVisible();
    await expect(card.getByRole("button", { name: "Reconnect" })).toBeVisible();
    await shot(page, "1440-light-aliexpress-reconnect-required");
  });

  test("a transient error is Needs attention, still connected-looking rows never say Connected", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({
        aliexpress: {
          connected: false,
          connection: aliExpressConnection({ status: "error", lastError: "AliExpress did not respond in time." }),
        },
      }),
    );
    const card = aliexpressCard(page);
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Needs attention");
    await expect(card.getByText("AliExpress did not respond in time.")).toBeVisible();
  });

  test("a server without AliExpress credentials: the 422 becomes Setup unavailable, not an env-var lecture", async ({
    page,
  }) => {
    await openIntegrations(page, channelsWorld({ aliexpressNotConfigured: true }));
    const card = aliexpressCard(page);
    await suppliers(page).getByRole("button", { name: "Connect AliExpress" }).click();
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Setup unavailable", { timeout: 10_000 });
    await expect(card.getByText(/ask your DropPilot operator/i)).toBeVisible();
    await expect(card.getByRole("button", { name: "Connect AliExpress" })).toHaveCount(0);
    await assertNoSecretsOrRawErrors(page);
    await shot(page, "1440-light-aliexpress-setup-unavailable");
  });

  test("a failed status request is Status unavailable with a retry", async ({ page }) => {
    const world = channelsWorld();
    world.fail.add("aliexpress");
    await mockChannelsApi(page, world);
    await page.goto("/settings/integrations");
    const card = aliexpressCard(page);
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Status unavailable", { timeout: 30_000 });
    world.fail.delete("aliexpress");
    await card.getByRole("button", { name: "Try again" }).click();
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Not connected", { timeout: 10_000 });
  });
});

test.describe("Channels — eBay", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("not configured: Setup unavailable and a disabled, described Connect", async ({ page }) => {
    await openIntegrations(page, channelsWorld({ ebay: { configured: false, connected: false, connection: null } }));
    const card = ebayCard(page);
    await expect(card.getByTestId("channel-ebay-status")).toHaveText("Setup unavailable");
    await expect(card.getByText("Unavailable")).toBeVisible();
    await expect(card.getByRole("button", { name: "Connect eBay" })).toBeDisabled();
    await expect(card.getByText("Coming soon")).toHaveCount(0);
  });

  test("reconnect required: the machine reason becomes English and Reconnect is primary", async ({ page }) => {
    await openIntegrations(
      page,
      channelsWorld({
        ebay: {
          configured: true,
          connected: false,
          connection: ebayConnection({ status: "reconnect_required", needsReconnect: true, reconnectReason: "refresh_token_revoked" }),
        },
      }),
    );
    const card = ebayCard(page);
    await expect(card.getByTestId("channel-ebay-status")).toHaveText("Reconnect required");
    await expect(page.getByTestId("ebay-reconnect-notice")).toContainText(/password or username change/i);
    await expect(card.getByText("refresh_token_revoked")).toHaveCount(0);
    await card.getByRole("button", { name: "Reconnect" }).click();
    await page.waitForURL(/auth\.ebay\.com/, { timeout: 15_000 });
  });

  test("connected: seller, marketplace and a confirmed disconnect", async ({ page }) => {
    await openIntegrations(page, channelsWorld({ ebay: { configured: true, connected: true, connection: ebayConnection() } }));
    const card = ebayCard(page);
    await expect(card.getByTestId("channel-ebay-status")).toHaveText("Connected");
    await expect(page.getByTestId("ebay-username")).toHaveText("brighthome_uk");
    await expect(card.getByText("United Kingdom")).toBeVisible();
    await expect(page.getByTestId("overview-ebay")).toContainText("brighthome_uk");
    await shot(page, "1440-light-ebay-connected");
    await card.getByRole("button", { name: "Disconnect" }).click();
    await page.getByTestId("disconnect-confirm-ebay").click();
    await expect(card.getByTestId("channel-ebay-status")).toHaveText("Not connected", { timeout: 10_000 });
  });
});

test.describe("Channels — canonical surface and Stores", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("Channels › Integrations is the one connect surface; the overview answers at a glance", async ({ page }) => {
    await mockChannelsApi(
      page,
      channelsWorld({
        shopify: { configured: true, connections: [shopifyConnection()] },
        aliexpress: { connected: true, connection: aliExpressConnection() },
        ebay: { configured: true, connected: false, connection: null },
      }),
    );
    await page.goto("/dashboard");
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await nav.getByRole("link", { name: "Integrations" }).click();
    await expect(page).toHaveURL(/\/settings\/integrations$/);
    await expect(page.getByRole("heading", { level: 1, name: "Integrations" })).toBeVisible();
    const overview = page.getByTestId("channel-overview");
    await expect(overview.getByTestId("overview-aliexpress")).toContainText("Connected");
    await expect(overview.getByTestId("overview-shopify")).toContainText("1 connected");
    await expect(overview.getByTestId("overview-ebay")).toContainText("Not connected");
    // Exactly one merchant-facing way to start each provider's connection.
    await expect(page.getByRole("button", { name: /^Connect (Shopify|another store)$/ })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Add store" })).toHaveCount(0);
    // Planned channels are named, not offered.
    const planned = page.getByTestId("planned-channels");
    await expect(planned).toContainText("Coming soon");
    await expect(planned).toContainText("Etsy");
    await expect(planned.getByRole("button")).toHaveCount(0);
    await expect(planned.getByRole("link")).toHaveCount(0);
    await shot(page, "1440-light-overview-mixed");
  });

  test("Stores is a supporting record view: no manual Add store, statuses in the shared words, Manage → Integrations", async ({
    page,
  }) => {
    const log = await mockChannelsApi(
      page,
      channelsWorld({
        stores: [
          storeRecord(),
          storeRecord({ id: "33333333-3333-4333-8333-333333333333", name: "Old Shop", slug: "old-shop", status: "disconnected", lastError: "Disconnected from DropPilot." }),
          storeRecord({ id: "44444444-4444-4444-8444-444444444444", name: "Manual test", slug: "manual-test", platform: "manual", status: "pending", storefrontUrl: null, lastSyncAt: null, lastActivityAt: null }),
          storeRecord({ id: "55555555-5555-4555-8555-555555555555", name: "Broken", slug: "broken", status: "error", lastError: 'HTTP 500 {"x":1}' }),
        ],
      }),
    );
    await page.goto("/stores");
    await expect(page.getByRole("heading", { level: 1, name: "Stores" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Add store" })).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const statuses = page.locator('[data-testid="store-status"]:visible');
    await expect(statuses).toHaveText(["Connected", "Disconnected", "Setup incomplete", "Needs attention"]);
    await expect(page.getByText(/HTTP 500/)).toHaveCount(0);
    // Raw enum words never appear as status text.
    for (const raw of ["pending", "disconnected", "error"]) {
      await expect(page.locator('[data-testid="store-status"]:visible', { hasText: new RegExp(`^${raw}$`) })).toHaveCount(0);
    }
    await expect(page.getByText("Health")).toHaveCount(0);
    await shot(page, "1440-light-stores");
    await page.getByRole("link", { name: "Manage connections" }).click();
    await expect(page).toHaveURL(/\/settings\/integrations$/);
    expect(log.requests.filter((r) => r.method === "POST" && r.path === "/stores")).toHaveLength(0);
  });

  test("Stores empty state leads to Integrations", async ({ page }) => {
    await mockChannelsApi(page, channelsWorld());
    await page.goto("/stores");
    await expect(page.getByTestId("stores-empty")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("link", { name: "Go to Integrations" }).click();
    await expect(page).toHaveURL(/\/settings\/integrations$/);
  });

  test("callback banners still render the backend's outcome vocabulary, and nothing else", async ({ page }) => {
    await mockChannelsApi(page, channelsWorld({ shopify: { configured: true, connections: [shopifyConnection({ webhookHealth: "degraded", webhooksRegisteredAt: null })] } }));
    await page.goto("/settings/integrations?shopify=connected_webhooks_degraded");
    const banner = page.getByRole("alert").first();
    await expect(banner.getByRole("heading", { name: /webhook setup incomplete/i })).toBeVisible({ timeout: 30_000 });
    await page.goto("/settings/integrations?aliexpress=denied");
    await expect(page.getByRole("heading", { name: "Authorization declined" })).toBeVisible({ timeout: 30_000 });
    await page.goto("/settings/integrations?shopify=%3Cscript%3Ealert(1)%3C/script%3E");
    await expect(page.getByTestId("channel-shopify-status")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("alert(1)")).toHaveCount(0);
    await expect(page.getByRole("alert").filter({ has: page.getByRole("heading") })).toHaveCount(0);
  });
});

test.describe("Channels — responsive and themes", () => {
  test("390: cards stack, targets are 44px, dialogs fit, no overflow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openIntegrations(
      page,
      channelsWorld({
        shopify: { configured: true, connections: [shopifyConnection({ shopDomain: "a-very-long-store-name-for-the-demo-workspace-limited.myshopify.com" })] },
        aliexpress: { connected: true, connection: aliExpressConnection() },
        ebay: { configured: true, connected: false, connection: ebayConnection({ status: "reconnect_required", needsReconnect: true, reconnectReason: "no_refresh_token" }) },
      }),
    );
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-integrations");
    const card = shopifyCard(page);
    await card.scrollIntoViewIfNeeded();
    for (const name of ["Disconnect", "Connect another store"]) {
      const box = await card.getByRole("button", { name }).boundingBox();
      expect(box?.height ?? 0, name).toBeGreaterThanOrEqual(44);
    }
    await card.getByRole("button", { name: "Disconnect" }).click();
    const dialog = page.getByTestId("disconnect-dialog-shopify");
    await expect(dialog).toBeVisible();
    // Radix moves focus into the dialog; which control it lands on is its
    // choice, that it is inside is ours.
    await expect
      .poll(() =>
        page.evaluate(
          () => document.activeElement?.closest('[data-testid="disconnect-dialog-shopify"]') !== null,
        ),
      )
      .toBe(true);
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-disconnect-dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Disconnect" })).toBeFocused();

    await mockChannelsApi(page, channelsWorld({ stores: [storeRecord(), storeRecord({ id: "33333333-3333-4333-8333-333333333333", name: "Old Shop", slug: "old-shop", status: "disconnected" })] }));
    await page.goto("/stores");
    await expect(page.getByTestId("store-cards")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-testid="store-card"]')).toHaveCount(2);
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-stores");
  });

  test("1024 light and dark", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openIntegrations(
      page,
      channelsWorld({
        shopify: {
          configured: true,
          connections: [shopifyConnection(), shopifyConnection({ id: "x2", storeId: "22222222-2222-4222-8222-222222222222", shopDomain: "second-shop.myshopify.com", webhookHealth: "degraded", webhooksRegisteredAt: null })],
        },
        aliexpress: { connected: false, connection: aliExpressConnection({ status: "expired", isTokenExpired: true }) },
        ebay: { configured: false, connected: false, connection: null },
      }),
    );
    await assertNoHorizontalOverflow(page);
    await shot(page, "1024-light-mixed");
    await page.emulateMedia({ colorScheme: "dark" });
    await shot(page, "1024-dark-mixed");
  });

  test("1440 dark", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ colorScheme: "dark" });
    await openIntegrations(
      page,
      channelsWorld({
        shopify: { configured: true, connections: [shopifyConnection({ status: "error", lastError: "HTTP 401" })] },
        aliexpress: { connected: true, connection: aliExpressConnection() },
        ebay: { configured: true, connected: true, connection: ebayConnection() },
      }),
    );
    await shot(page, "1440-dark-mixed");
    await shopifyCard(page).getByRole("button", { name: "Disconnect" }).click();
    await expect(page.getByTestId("disconnect-dialog-shopify")).toBeVisible();
    await shot(page, "1440-dark-disconnect-dialog");
    await page.keyboard.press("Escape");
    await mockChannelsApi(page, channelsWorld({ stores: [storeRecord(), storeRecord({ id: "33333333-3333-4333-8333-333333333333", name: "Old Shop", slug: "old-shop", status: "disconnected" })] }));
    await page.goto("/stores");
    await expect(page.getByTestId("stores")).toBeVisible({ timeout: 30_000 });
    await shot(page, "1440-dark-stores");
  });
});

test.describe("Channels — keyboard", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the disconnect flow is operable by keyboard and returns focus", async ({ page }) => {
    await openIntegrations(page, channelsWorld({ aliexpress: { connected: true, connection: aliExpressConnection() } }));
    const card = aliexpressCard(page);
    await card.getByRole("button", { name: "Disconnect" }).focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByTestId("disconnect-dialog-aliexpress");
    await expect(dialog).toBeVisible();
    await page.getByTestId("disconnect-confirm-aliexpress").focus();
    await page.keyboard.press("Enter");
    await expect(card.getByTestId("channel-aliexpress-status")).toHaveText("Not connected", { timeout: 10_000 });
    await expect(dialog).toHaveCount(0);
  });
});
