import { mockShellApi } from "./helpers/shell-fixture";
import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  confirmReauth,
  goTo,
  mockPlatform,
  signIn,
} from "./platform-mock";

/**
 * Admin Control Center phase 10 (D-019). Pins down:
 * - starting maintenance asks for re-authentication and sends the message;
 * - the merchant app shows maintenance and live announcements as banners.
 */

const SETTINGS = {
  maintenance: { enabled: false, message: null, updatedAt: null },
  announcements: [],
};

test("a super admin starts maintenance with a message", async ({ page }) => {
  let state = { ...SETTINGS };
  const log = await mockPlatform(page, {
    reauthPaths: ["/settings/maintenance"],
    handlers: {
      "GET /settings": () => state,
      "POST /settings/maintenance": (body) => {
        const b = body as { enabled: boolean; message: string | null };
        state = {
          ...state,
          maintenance: {
            enabled: b.enabled,
            message: b.message,
            updatedAt: NOW,
          },
        };
        return state;
      },
    },
  });
  await signIn(page);
  await goTo(page, "Settings");

  const card = page.getByTestId("platform-maintenance");
  await card
    .getByLabel("Message for merchants (optional)")
    .fill("Back by 14:00 UTC");
  await card
    .getByLabel("Reason for Start maintenance")
    .fill("database upgrade");
  await card.getByRole("button", { name: "Start maintenance" }).click();
  await card
    .getByRole("button", { name: "Confirm: start maintenance" })
    .click();
  await confirmReauth(page);
  await expect(card).toContainText("on");
  await expect(card).toContainText("Message: Back by 14:00 UTC");
  expect(
    log
      .filter((e) => e.path === "/settings/maintenance" && e.method === "POST")
      .pop()?.body,
  ).toEqual({
    enabled: true,
    message: "Back by 14:00 UTC",
    reason: "database upgrade",
  });
});

test("merchants see maintenance and live announcements as banners", async ({
  page,
}) => {
  await mockShellApi(page);
  await page.route("**/api/v1/system/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        maintenance: true,
        maintenanceMessage: "Back by 14:00 UTC.",
        announcements: [
          {
            id: "a1",
            title: "New: bulk AI",
            body: "Optimise 50 products at once.",
            level: "info",
            endsAt: null,
          },
        ],
      }),
    }),
  );
  await page.goto("/dashboard");
  await expect(page.getByTestId("system-maintenance")).toContainText(
    "changes are paused",
  );
  await expect(page.getByTestId("system-maintenance")).toContainText(
    "Back by 14:00 UTC.",
  );
  await expect(page.getByTestId("system-announcement")).toContainText(
    "New: bulk AI",
  );
});
