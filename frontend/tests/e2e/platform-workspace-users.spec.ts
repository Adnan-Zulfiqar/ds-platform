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
 * Admin Control Center phase 4 (D-019). Pins down:
 * - a change without a support session is explained, not swallowed;
 * - opening one asks for re-authentication and sends the reason and length;
 * - a destructive change needs a second click and sends exactly the reason;
 * - an owner's role cannot be changed from the console.
 */

const W = `/workspaces/${TENANT_ID}`;
const MEMBER = {
  id: "u2",
  email: "member@acme.test",
  firstName: "Max",
  lastName: "Member",
  isActive: true,
  isVerified: true,
  lastLoginAt: NOW,
  createdAt: NOW,
  roles: ["member"],
  activeSessions: 1,
};
const OWNER = {
  ...MEMBER,
  id: "u1",
  email: "owner@acme.test",
  roles: ["owner"],
};

test("an operator opens a support session, then disables a member with a reason", async ({
  page,
}) => {
  let session: unknown = null;
  let reauthed = false;
  const log = await mockPlatform(page, {
    handlers: {
      "POST /auth/reauth": () => {
        reauthed = true;
        return { reauthenticatedAt: NOW, validUntil: NOW };
      },
      [`GET ${W}/users`]: () => page1([OWNER, MEMBER]),
      [`GET ${W}/invitations`]: () => page1([]),
      [`GET ${W}/support-session`]: () => session,
      [`POST ${W}/support-session`]: (body) => {
        if (!reauthed) {
          return {
            status: 403,
            body: {
              code: "reauth_required",
              message: "Confirm",
              details: [],
              requestId: "r",
            },
          };
        }
        session = {
          id: "ss1",
          reason: (body as { reason: string }).reason,
          createdAt: NOW,
          expiresAt: NOW,
          endedAt: null,
        };
        return session;
      },
      [`POST ${W}/users/u2/disable`]: () =>
        session
          ? { id: "u2", isActive: false, roles: ["member"], sessionsEnded: 1 }
          : {
              status: 403,
              body: {
                code: "support_session_required",
                message: "Open one",
                details: [],
                requestId: "r",
              },
            },
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await page.getByRole("button", { name: "Users" }).click();
  await page
    .getByTestId("platform-workspace-users")
    .getByText("member@acme.test")
    .click();

  const actions = page.getByTestId("platform-user-actions");
  const disable = actions.getByRole("button", { name: "Disable user" });
  await actions.getByLabel("Reason for Disable user").fill("abuse report");
  await disable.click();
  await actions.getByRole("button", { name: "Confirm: disable user" }).click();
  await expect(actions).toContainText("Open a support session");
  await page.keyboard.press("Escape");

  const banner = page.getByTestId("platform-support-session");
  await banner.getByLabel(/Reason/).fill("ticket 4411");
  await banner.getByLabel("Minutes").selectOption("60");
  await banner.getByRole("button", { name: "Open support session" }).click();
  await confirmReauth(page);
  await expect(banner).toContainText("Reason: ticket 4411");
  expect(
    log
      .filter((e) => e.path === `${W}/support-session` && e.method === "POST")
      .pop()?.body,
  ).toEqual({
    reason: "ticket 4411",
    minutes: 60,
  });

  await page
    .getByTestId("platform-workspace-users")
    .getByText("member@acme.test")
    .click();
  await actions.getByLabel("Reason for Disable user").fill("abuse report");
  await actions.getByRole("button", { name: "Disable user" }).click();
  await actions.getByRole("button", { name: "Confirm: disable user" }).click();
  await expect(actions).toContainText("Done. Recorded in the audit log.");
  expect(
    log.filter((e) => e.path === `${W}/users/u2/disable`).pop()?.body,
  ).toEqual({
    reason: "abuse report",
  });
});

test("an owner's role is not offered for change", async ({ page }) => {
  await mockPlatform(page, {
    handlers: {
      [`GET ${W}/users`]: () => page1([OWNER]),
      [`GET ${W}/invitations`]: () => page1([]),
      [`GET ${W}/support-session`]: () => null,
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await page.getByRole("button", { name: "Users" }).click();
  await page
    .getByTestId("platform-workspace-users")
    .getByText("owner@acme.test")
    .click();
  const actions = page.getByTestId("platform-user-actions");
  await expect(actions).toContainText(
    "An owner's role is not changed from the console.",
  );
  await expect(
    actions.getByRole("button", { name: "Change role" }),
  ).toHaveCount(0);
});
