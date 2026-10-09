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
 * Admin Control Center phase 7 (D-019). Pins down:
 * - the summary and the stuck list come from the jobs API;
 * - closing a stuck sync goes to that job's own workspace route with the
 *   typed reason, after re-authentication.
 */

const STUCK = {
  kind: "order_sync",
  id: "run1",
  tenantId: TENANT_ID,
  tenantName: "Acme Trading",
  status: "running",
  error: null,
  startedAt: NOW,
  finishedAt: null,
  createdAt: NOW,
};

test("an operator closes a stuck order sync from the jobs page", async ({
  page,
}) => {
  const log = await mockPlatform(page, {
    reauthPaths: [`/workspaces/${TENANT_ID}/jobs/order_sync/run1/close`],
    handlers: {
      "GET /jobs/summary": () => ({
        order_sync: { failed: 0, stuck: 1 },
        inventory_sync: { failed: 2, stuck: 0 },
        product_import: { failed: 0, stuck: 0 },
        pipeline_run: { failed: 0, stuck: 0 },
        rule_application: { failed: 0, stuck: 0 },
        supplier_order: { failed: 0, stuck: 0 },
        automation_run: { failed: 0, stuck: 0 },
      }),
      "GET /jobs": () => page1([STUCK]),
      [`POST /workspaces/${TENANT_ID}/jobs/order_sync/run1/close`]: () => ({
        kind: "order_sync",
        id: "run1",
        outcome: "failed",
      }),
    },
  });
  await signIn(page);
  await goTo(page, "Jobs");
  await expect(page.getByTestId("platform-jobs-summary")).toContainText(
    "0 failed · 1 stuck",
  );

  await page.getByRole("button", { name: "Stuck", exact: true }).click();
  const table = page.getByTestId("platform-jobs");
  await expect(table).toContainText("Acme Trading");
  page.once(
    "dialog",
    (dialog) => void dialog.accept("blocked every later sync"),
  );
  await table.getByRole("button", { name: "Close stuck run" }).click();
  await confirmReauth(page);
  await expect(table).toContainText("Done");
  expect(
    log
      .filter(
        (e) => e.path === `/workspaces/${TENANT_ID}/jobs/order_sync/run1/close`,
      )
      .pop()?.body,
  ).toEqual({ reason: "blocked every later sync" });
  expect(log.find((e) => e.path === "/jobs")?.method).toBe("GET");
});
