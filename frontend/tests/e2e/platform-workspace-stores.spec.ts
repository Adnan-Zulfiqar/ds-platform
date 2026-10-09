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
 * Admin Control Center phase 5 (D-019). Pins down:
 * - pausing a store sends exactly the reason to that store's pause route;
 * - the paused state then shows on the list;
 * - "sync now" calls the workspace sync route.
 */

const W = `/workspaces/${TENANT_ID}`;
const STORE = {
  id: "s1",
  name: "Acme Shopify",
  platform: "shopify",
  status: "connected",
  currency: "USD",
  inventorySyncEnabled: true,
  pricingSyncEnabled: true,
  orderSyncEnabled: true,
  lastSyncAt: NOW,
  lastError: null,
  healthScore: 90,
  syncPausedAt: null as string | null,
  syncPausedReason: null as string | null,
};

test("an operator pauses a store and runs an inventory sync", async ({
  page,
}) => {
  let store = { ...STORE };
  const log = await mockPlatform(page, {
    handlers: {
      [`GET ${W}/stores`]: () => page1([store]),
      [`GET ${W}/connections`]: () => [],
      [`GET ${W}/support-session`]: () => ({
        id: "ss1",
        reason: "ticket",
        createdAt: NOW,
        expiresAt: NOW,
        endedAt: null,
      }),
      [`POST ${W}/stores/s1/pause`]: (body) => {
        store = {
          ...store,
          syncPausedAt: NOW,
          syncPausedReason: (body as { reason: string }).reason,
        };
        return store;
      },
      [`POST ${W}/sync/inventory`]: () => ({
        kind: "inventory",
        id: "r1",
        status: "succeeded",
        trigger: "manual",
        storeId: null,
        errorCode: null,
        errorMessage: null,
        startedAt: NOW,
        finishedAt: NOW,
        createdAt: NOW,
      }),
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await page.getByRole("button", { name: "Stores" }).click();

  await page
    .getByTestId("platform-workspace-stores")
    .getByText("Acme Shopify")
    .click();
  const actions = page.getByTestId("platform-store-actions");
  await actions.getByLabel("Reason for Pause updates").fill("duplicate pushes");
  await actions.getByRole("button", { name: "Pause updates" }).click();
  await actions.getByRole("button", { name: "Confirm: pause updates" }).click();
  await expect(actions).toContainText("Done. Recorded in the audit log.");
  expect(
    log.filter((e) => e.path === `${W}/stores/s1/pause`).pop()?.body,
  ).toEqual({
    reason: "duplicate pushes",
  });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("platform-workspace-stores")).toContainText(
    "paused",
  );

  const sync = page.getByTestId("platform-sync-now");
  await sync.getByLabel("Reason for Sync inventory").fill("merchant asked");
  await sync.getByRole("button", { name: "Sync inventory" }).click();
  await expect(sync).toContainText("Done. Recorded in the audit log.");
  expect(
    log.filter((e) => e.path === `${W}/sync/inventory`).pop()?.body,
  ).toEqual({
    reason: "merchant asked",
  });
});
