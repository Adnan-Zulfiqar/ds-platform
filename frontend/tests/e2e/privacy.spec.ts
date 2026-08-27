import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * EBAY-C1.1 — the public privacy policy.
 *
 * eBay fetches the policy URL to validate the RuName, with no session and no
 * cooperation from us, so the tests that matter most here are the boring ones:
 * it exists, it is reachable signed out, and it does not bounce to a login.
 *
 * The content assertions are deliberately about *obligations* — the ICO route,
 * the contact address, the eBay section, the deletion behaviour — rather than
 * prose wording, so an editorial pass does not break the suite while a removed
 * legal requirement does.
 */

function main(page: Page) {
  return page.getByRole("main");
}

test.describe("Privacy policy — public reachability", () => {
  test("is served without a session and does not redirect to sign in", async ({
    page,
  }) => {
    const response = await page.goto("/privacy");

    expect(response?.status()).toBe(200);
    await expect(page).toHaveURL(/\/privacy$/);
    expect(page.url()).not.toContain("/login");
  });

  test("renders as a real policy, not a placeholder", async ({ page }) => {
    await page.goto("/privacy");

    await expect(
      page.getByRole("heading", { name: "Privacy Policy", level: 1 }),
    ).toBeVisible();
    await expect(main(page).getByText("DropPilot AI").first()).toBeVisible();
    await expect(main(page).getByText(/Last updated:/)).toBeVisible();

    // A stub would be short. A genuine policy is not.
    const text = await main(page).innerText();
    expect(text.length).toBeGreaterThan(3000);
  });

  test("mutates nothing of its own while rendering", async ({ page }) => {
    /**
     * The page itself must write nothing. It does not, and this proves it.
     *
     * One request is excluded deliberately: the application shell's
     * `POST /api/v1/auth/refresh`. `AuthProvider` lives in the root layout and
     * attempts a session handshake on *every* route — `/login` and `/register`
     * issue exactly the same call with no session. Asserting an empty list
     * would therefore be asserting something about the shell, not the policy,
     * and would fail for a reason that has nothing to do with this page.
     *
     * Everything else is still forbidden, which is what would catch a privacy
     * page that started recording visits.
     */
    const mutations: string[] = [];
    page.on("request", (r) => {
      if (!["POST", "PUT", "PATCH", "DELETE"].includes(r.method())) return;
      const path = new URL(r.url()).pathname;
      if (path === "/api/v1/auth/refresh") return;
      mutations.push(`${r.method()} ${path}`);
    });

    await page.goto("/privacy");
    await page.waitForTimeout(1200);

    expect(mutations).toEqual([]);
  });
});

test.describe("Privacy policy — required content", () => {
  const REQUIRED_HEADINGS = [
    "Who this policy covers",
    "Information we collect from you",
    "Information we receive from connected marketplaces",
    "eBay seller data",
    "Why we process this information",
    "Lawful bases",
    "Who we share information with",
    "International transfers",
    "Retention and deletion",
    "Security",
    "Cookies and browser storage",
    "Your rights",
    "Withdrawing consent",
    "Complaints",
    "Children",
    "Changes to this policy",
    "Contact us",
  ];

  for (const heading of REQUIRED_HEADINGS) {
    test(`covers "${heading}"`, async ({ page }) => {
      await page.goto("/privacy");
      await expect(
        page.getByRole("heading", { name: new RegExp(heading, "i") }),
      ).toBeVisible();
    });
  }

  test("gives a working contact address", async ({ page }) => {
    await page.goto("/privacy");
    const contact = page.getByRole("link", { name: "privacy@whiteto.com" }).first();
    await expect(contact).toBeVisible();
    await expect(contact).toHaveAttribute("href", "mailto:privacy@whiteto.com");
  });

  test("points at the ICO for complaints", async ({ page }) => {
    await page.goto("/privacy");
    const ico = page.getByRole("link", { name: /Information Commissioner/i });
    await expect(ico).toBeVisible();
    await expect(ico).toHaveAttribute("href", /ico\.org\.uk/);
  });

  test("states the eBay facts C1 actually implements", async ({ page }) => {
    await page.goto("/privacy");
    const text = await main(page).innerText();

    // Encrypted credentials, immutable identity, deletion on disconnect, the
    // non-personal compliance record, and the honest scope limit.
    expect(text).toMatch(/encrypted/i);
    expect(text).toMatch(/immutable user identifier/i);
    expect(text).toMatch(/Disconnecting/i);
    expect(text).toMatch(/marketplace account deletion/i);
    expect(text).toMatch(/does not yet import listings, inventory or orders/i);
  });

  test("does not overclaim, and does not leak configuration", async ({ page }) => {
    await page.goto("/privacy");
    const text = await main(page).innerText();
    const html = await page.content();

    // No absolute security promise.
    expect(text).toMatch(/No online service can promise absolute security/i);
    expect(text).not.toMatch(/\b(completely|totally|100%)\s+secure\b/i);

    // No internal paths, hosts, ports or credential-shaped strings.
    for (const leak of [
      "127.0.0.1",
      "localhost",
      "C:\\",
      "dsplive",
      "EBAY_CLIENT",
      "SECURITY_ENCRYPTION_KEYS",
      "Auto_Pilot",
      "gAAAAA",
    ]) {
      expect(html).not.toContain(leak);
    }
  });
});

test.describe("Privacy policy — presentation", () => {
  test("renders in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/privacy");
    await expect(
      page.getByRole("heading", { name: "Privacy Policy", level: 1 }),
    ).toBeVisible();
  });

  for (const [label, width] of [
    ["mobile", 375],
    ["tablet", 768],
    ["desktop", 1440],
  ] as const) {
    test(`does not overflow horizontally at ${label}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/privacy");

      const overflows = await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth,
      );
      expect(overflows).toBe(false);
    });
  }

  test("links are reachable and focusable by keyboard", async ({ page }) => {
    await page.goto("/privacy");
    const contact = page.getByRole("link", { name: "privacy@whiteto.com" }).first();
    await contact.focus();
    expect(await contact.evaluate((el) => el === document.activeElement)).toBe(true);
  });

  test("is indexable, unlike the rest of the authenticated app", async ({
    page,
  }) => {
    // The root layout sets `noindex` because everything else sits behind a
    // login. This page overrides it: a policy search engines are told to ignore
    // is not a published policy.
    await page.goto("/privacy");

    const robots = await page
      .locator('meta[name="robots"]')
      .first()
      .getAttribute("content");

    expect(robots ?? "").not.toMatch(/noindex/i);
    expect(robots ?? "").toMatch(/index/i);
  });

  test("does not repeat the operator name in the tab title", async ({ page }) => {
    await page.goto("/privacy");
    const title = await page.title();

    expect(title).toMatch(/Privacy Policy/i);
    // The root layout already appends the brand via its title template.
    expect(title.match(/DropPilot AI/g)?.length ?? 0).toBe(1);
  });

  test("uses one h1 and semantic section headings", async ({ page }) => {
    await page.goto("/privacy");
    expect(await page.getByRole("heading", { level: 1 }).count()).toBe(1);
    expect(await page.getByRole("heading", { level: 2 }).count()).toBeGreaterThanOrEqual(17);
  });
});

test.describe("Privacy policy — discoverability", () => {
  for (const route of ["/login", "/register", "/forgot-password"]) {
    test(`is linked from ${route}`, async ({ page }) => {
      await page.goto(route);
      const link = page.getByRole("link", { name: "Privacy Policy" });
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute("href", "/privacy");
    });
  }

  test("is linked from the integrations page, where accounts are connected", async ({
    page,
  }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const link = page.getByRole("link", { name: "Privacy Policy" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/privacy");
  });

  test("the eBay card still has no \"Coming soon\"", async ({ page }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const card = page.getByTestId("ebay-card");
    await card.waitFor({ state: "visible", timeout: 20000 });
    await expect(card.getByText("Coming soon")).toHaveCount(0);
  });
});
