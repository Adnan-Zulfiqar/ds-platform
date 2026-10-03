import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * Track E3 — Settings → Notifications, backend-less. Pins down: the saved
 * choices render checked, Save stays disabled until something changes, and
 * the PUT carries exactly the chosen kinds.
 */

const AVAILABLE = [
  "import_completed",
  "sync_failed",
  "inventory_changed",
  "price_changed",
  "order_imported",
  "shipment_updated",
  "webhook_failure",
  "task_failure",
  "automation_completed",
  "automation_failed",
];
const DEFAULTS = ["automation_failed", "sync_failed", "task_failure", "webhook_failure"];

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

test("a user opts into routine news by email and the PUT carries exactly their choice", async ({
  page,
}) => {
  await mockChannelsApi(page, channelsWorld());
  const sent: unknown[] = [];
  await page.route("**/api/v1/notifications/email-preferences", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { kinds: string[] };
      sent.push(body);
      return json(route, { kinds: body.kinds, available: AVAILABLE });
    }
    return json(route, { kinds: DEFAULTS, available: AVAILABLE });
  });

  await page.goto("/settings/notifications");
  await expect(page.getByLabel("Sync failed")).toBeChecked();
  await expect(page.getByLabel("Order imported")).not.toBeChecked();
  const save = page.getByRole("button", { name: "Save" });
  await expect(save).toBeDisabled();

  await page.getByLabel("Order imported").check();
  await page.getByLabel("Sync failed").uncheck();
  await save.click();

  await expect(page.getByRole("status")).toHaveText("Saved.");
  expect(sent).toEqual([
    { kinds: ["automation_failed", "order_imported", "task_failure", "webhook_failure"] },
  ]);
  await expect(page.getByLabel("Order imported")).toBeChecked();
  await expect(save).toBeDisabled();
});

test("the settings index links to notifications", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.goto("/settings");
  await expect(page.getByRole("link", { name: /Notifications/ })).toHaveAttribute(
    "href",
    "/settings/notifications",
  );
});
