import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  buildAccount,
  isApiReachable,
  LEGAL_ACCEPTANCE_BODY,
} from "./helpers/auth";

/**
 * Settings → Global Rules → Stock, against the real backend.
 *
 * Pins down: the setting starts on the supplier's stock, a quantity saves and
 * survives a reload, an impossible quantity is refused before it is sent,
 * and the panel says what happens to a sold-out product in words.
 */

test.beforeEach(async () => {
  test.skip(!(await isApiReachable()), "Backend is not reachable.");
});

async function openStock(page: Page): Promise<void> {
  const account = buildAccount();
  const response = await page.request.post(`${API_URL}/api/v1/auth/register`, {
    data: {
      companyName: account.companyName,
      email: account.email,
      password: account.password,
      firstName: "E2E",
      lastName: "Operator",
      ...LEGAL_ACCEPTANCE_BODY,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  await page.goto("/settings/global-rules?section=stock");
  await expect(page.getByTestId("stock-settings")).toBeVisible({ timeout: 30_000 });
}

test("a set quantity saves, survives a reload and can be cleared", async ({ page }) => {
  await openStock(page);
  const panel = page.getByTestId("stock-settings");
  await expect(panel.getByRole("radio", { name: /Supplier's stock/ })).toBeChecked();
  await expect(panel).toContainText("listed at 0");

  await panel.getByRole("radio", { name: /The same quantity for every product/ }).check();
  await panel.getByLabel("Quantity to list").fill("14");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("status")).toHaveText(
    "Saved. Channels will list 14 of each product.",
  );

  await page.reload();
  const reloaded = page.getByTestId("stock-settings");
  await expect(reloaded.getByLabel("Quantity to list")).toHaveValue("14", { timeout: 30_000 });

  await reloaded.getByRole("radio", { name: /Supplier's stock/ }).check();
  await reloaded.getByRole("button", { name: "Save" }).click();
  await expect(reloaded.getByRole("status")).toHaveText(
    "Saved. Channels will list the supplier's stock.",
  );
});

test("an impossible quantity is refused before it is sent", async ({ page }) => {
  await openStock(page);
  const panel = page.getByTestId("stock-settings");
  const puts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().endsWith("/global-rules/stock")) {
      puts.push(request.url());
    }
  });
  await panel.getByRole("radio", { name: /The same quantity for every product/ }).check();
  await panel.getByLabel("Quantity to list").fill("0");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("alert")).toHaveText("Enter a whole number from 1 to 10,000.");
  expect(puts).toEqual([]);
});
