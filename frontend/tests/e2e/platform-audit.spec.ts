import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  confirmReauth,
  goTo,
  mockPlatform,
  page1,
  signIn,
} from "./platform-mock";

/**
 * Admin Control Center phase 9 (D-019). Pins down:
 * - filters reach `/audit/search` as query parameters;
 * - a row opens its full detail;
 * - the export asks for re-authentication, then downloads.
 */

const ENTRY = {
  id: "a1",
  createdAt: NOW,
  adminId: "op1",
  adminEmail: "ops@example.com",
  actorRole: "super_admin",
  action: "workspace_user_disabled",
  outcome: "success",
  targetTenantId: "t1",
  targetType: "user",
  targetId: "u2",
  detail: { reason: "abuse report", sessions_ended: 1 },
  clientIp: "127.0.0.1",
  userAgent: "Playwright",
  requestId: "req-42",
};

test("an auditor filters the trail, opens a row and exports it", async ({
  page,
}) => {
  let reauthed = false;
  const searches: string[] = [];
  await mockPlatform(page, {
    role: "auditor",
    permissions: [
      "audit.export",
      "audit.read",
      "dashboard.read",
      "tenants.read",
    ],
    handlers: {
      "POST /auth/reauth": () => {
        reauthed = true;
        return { reauthenticatedAt: NOW, validUntil: NOW };
      },
      "GET /security": () => ({
        windowHours: 24,
        byAction: { login_failed: 3 },
        topIps: [{ client_ip: "203.0.113.9", failures: 3 }],
      }),
    },
  });
  await page.route("**/api/v1/platform/audit/search**", (route) => {
    searches.push(new URL(route.request().url()).search);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(page1([ENTRY])),
    });
  });
  await page.route("**/api/v1/platform/audit/export**", (route) =>
    reauthed
      ? route.fulfill({
          status: 200,
          contentType: "text/csv",
          headers: {
            "Content-Disposition": 'attachment; filename="platform-audit.csv"',
            "Access-Control-Expose-Headers": "Content-Disposition",
          },
          body: "created_at,action\n",
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
  await signIn(page);
  await goTo(page, "Audit log");

  await expect(page.getByTestId("platform-security")).toContainText(
    "Failed sign-ins",
  );
  await expect(page.getByTestId("platform-security")).toContainText(
    "203.0.113.9: 3",
  );

  await page.getByLabel("Action").fill("workspace_");
  await page.getByLabel("Outcome").selectOption("success");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect
    .poll(() => searches.some((q) => q.includes("action=workspace_")))
    .toBe(true);
  expect(searches.pop()).toContain("outcome=success");

  await page
    .getByTestId("platform-audit")
    .getByText("workspace_user_disabled")
    .click();
  const detail = page.getByTestId("platform-audit-detail");
  await expect(detail).toContainText("abuse report");
  await expect(detail).toContainText("req-42");
  await page.keyboard.press("Escape");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  await confirmReauth(page);
  expect((await download).suggestedFilename()).toBe("platform-audit.csv");
});
