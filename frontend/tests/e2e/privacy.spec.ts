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

  test("performs no authentication bootstrap and no API mutation", async ({
    page,
  }) => {
    /**
     * The whole point of the route, and the reason `AuthProvider` was moved out
     * of the root layout.
     *
     * There is no exemption here on purpose. An earlier version of this test
     * allowed `POST /auth/refresh` on the grounds that the application shell
     * issued it on every route — which was true, and was the defect. A page
     * anyone may read without an account must not call an authenticated
     * endpoint at all.
     */
    const authCalls: string[] = [];
    const mutations: string[] = [];

    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (path.includes("/auth/")) authCalls.push(`${r.method()} ${path}`);
      if (["POST", "PUT", "PATCH", "DELETE"].includes(r.method())) {
        mutations.push(`${r.method()} ${path}`);
      }
    });

    await page.goto("/privacy");
    await expect(
      page.getByRole("heading", { name: "Privacy Policy", level: 1 }),
    ).toBeVisible();
    // Give any mount effect a chance to fire before asserting it did not.
    await page.waitForTimeout(1500);

    expect(authCalls).toEqual([]);
    expect(mutations).toEqual([]);
  });

  test("writes no DropPilot cookie", async ({ page, context }) => {
    await page.goto("/privacy");
    await page.waitForTimeout(1000);

    const ours = (await context.cookies()).filter((c) =>
      c.name.startsWith("droppilot"),
    );
    expect(ours).toEqual([]);
  });

  test("renders completely with the API unreachable", async ({ page }) => {
    // No CORS dependency and no API availability dependency: every call to the
    // backend origin is aborted, and the page must still be whole.
    const apiBase = new URL(
      process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8099",
    );
    await page.route(`${apiBase.origin}/**`, (route) => route.abort());

    await page.goto("/privacy");

    await expect(
      page.getByRole("heading", { name: "Privacy Policy", level: 1 }),
    ).toBeVisible();
    expect(await page.getByRole("heading", { level: 2 }).count()).toBeGreaterThanOrEqual(17);
    expect((await main(page).innerText()).length).toBeGreaterThan(3000);
  });

  test("is complete with JavaScript disabled", async ({ browser }) => {
    // Server-rendered content, not a client-side shell. eBay's fetch of this
    // URL runs no JavaScript either.
    const context = await browser.newContext({ javaScriptEnabled: false });
    const noJs = await context.newPage();

    const response = await noJs.goto("/privacy");
    expect(response?.status()).toBe(200);

    await expect(
      noJs.getByRole("heading", { name: "Privacy Policy", level: 1 }),
    ).toBeVisible();
    expect(await noJs.getByRole("heading", { level: 2 }).count()).toBeGreaterThanOrEqual(17);
    expect((await noJs.getByRole("main").innerText()).length).toBeGreaterThan(3000);
    await expect(
      noJs.getByRole("link", { name: "privacy@whiteto.com" }).first(),
    ).toBeVisible();

    await context.close();
  });

  test("logs no console or hydration error", async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") problems.push(m.text());
    });
    page.on("pageerror", (e) => problems.push(String(e)));

    await page.goto("/privacy");
    await page.waitForTimeout(1500);

    expect(problems).toEqual([]);
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
