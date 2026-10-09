import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  TENANT_ID,
  confirmReauth,
  goTo,
  mockPlatform,
  signIn,
} from "./platform-mock";

/**
 * Admin Control Center phase 8 (D-019). Pins down:
 * - the Billing tab shows the workspace's entitlement and switches;
 * - extending a trial asks for re-authentication and sends days and reason;
 * - switching a feature off sends `enabled: false` for that key.
 */

const W = `/workspaces/${TENANT_ID}`;
const BILLING = {
  plan: null,
  status: "none",
  onTrial: true,
  paid: false,
  trialEndsAt: NOW,
  currentPeriodEnd: null,
  cancelAtPeriodEnd: false,
  listingLimit: 450,
  listingsUsed: 12,
  canWrite: true,
  canUseAi: false,
  hasStripeCustomer: false,
  planOverride: null,
  planOverrideAi: false,
  planOverrideUntil: null,
  planOverrideReason: null,
  billingEnforced: true,
  flags: [
    {
      key: "ai_bulk_pipeline",
      description: "Bulk AI optimisation runs (AI Studio).",
      platformDefault: true,
      override: null,
      effective: true,
    },
  ],
};

test("an operator extends a trial and switches a feature off", async ({
  page,
}) => {
  const log = await mockPlatform(page, {
    reauthPaths: [`${W}/billing/trial`],
    handlers: {
      [`GET ${W}/billing`]: () => BILLING,
      [`POST ${W}/billing/trial`]: () => BILLING,
      [`POST ${W}/feature-flags/ai_bulk_pipeline`]: () => ({
        ...BILLING,
        flags: [{ ...BILLING.flags[0], override: false, effective: false }],
      }),
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await page.getByRole("button", { name: "Billing", exact: true }).click();

  const billing = page.getByTestId("platform-workspace-billing");
  await expect(billing).toContainText("12 of 450");
  await billing.getByLabel("Days to add").selectOption("30");
  await billing
    .getByLabel("Reason for Extend trial")
    .fill("onboarding delayed");
  await billing.getByRole("button", { name: "Extend trial" }).click();
  await confirmReauth(page);
  await expect(billing).toContainText("Done. Recorded in the audit log.");
  expect(
    log.filter((e) => e.path === `${W}/billing/trial`).pop()?.body,
  ).toEqual({
    days: 30,
    reason: "onboarding delayed",
  });

  const flags = page.getByTestId("platform-workspace-flags");
  await flags
    .getByLabel("Reason for Switch off for this workspace")
    .fill("credit abuse");
  await flags
    .getByRole("button", { name: "Switch off for this workspace" })
    .click();
  await flags
    .getByRole("button", { name: "Confirm: switch off for this workspace" })
    .click();
  await expect(flags).toContainText("off (override)");
  expect(
    log.filter((e) => e.path === `${W}/feature-flags/ai_bulk_pipeline`).pop()
      ?.body,
  ).toEqual({
    enabled: false,
    reason: "credit abuse",
  });
});
