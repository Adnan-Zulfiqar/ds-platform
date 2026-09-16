import { expect, test } from "./fixtures/provider-isolation";

/**
 * LEGAL-T1 — the public Terms page.
 *
 * Two kinds of assertion, and both matter for different reasons.
 *
 * **Mechanical**: the page must be reachable with no account, must mount no
 * authentication provider, must make no API or third-party request, and must
 * work with JavaScript switched off. A contract that needs a session, a network
 * round trip or a working script to read is not a published contract — and a
 * page that quietly calls an authenticated endpoint would put a session
 * bootstrap in front of somebody deciding whether to sign up at all.
 *
 * **Substantive**: the wording carries statutory disclosures and commercial
 * positions that were agreed deliberately, and a well-meaning edit can remove
 * one without anybody noticing. These pin the disclosures, the B2B framing, the
 * liability carve-outs that cannot lawfully be excluded, and — just as
 * important — the absence of promises the product cannot keep.
 */

const COMPANY = "DESIRLY LIMITED";
const COMPANY_NUMBER = "16381500";
const REGISTERED_OFFICE = "200 Eton Road Eton Road, Ilford, England, IG1 2UN";
const SUPPORT_CONTACT = "support@whiteto.com";
const PRIVACY_CONTACT = "privacy@whiteto.com";

test.describe("Terms page — reachability and isolation", () => {
  test("is served with no account and no redirect", async ({ page }) => {
    const response = await page.goto("/terms");

    expect(response?.status()).toBe(200);
    expect(new URL(page.url()).pathname).toBe("/terms");
    await expect(page.getByRole("heading", { level: 1, name: "Terms of Service" })).toBeVisible();
  });

  test("makes no API, auth-refresh or third-party request", async ({ page, baseURL }) => {
    // Compared against the configured base URL, not `page.url()`: at the moment
    // the document request fires the page is still `about:blank`, which would
    // make the page flag its own navigation as off-site.
    const ownOrigin = new URL(baseURL ?? "http://localhost:3098").origin;
    const offSite: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.includes("/api/") || url.origin !== ownOrigin) {
        offSite.push(request.url());
      }
    });

    await page.goto("/terms", { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    // The privacy page proved this pattern; the same rule applies here. An
    // `/auth/refresh` on a public legal page was a real defect once.
    const external = offSite.filter((url) => !url.startsWith("data:"));
    expect(external, `unexpected requests: ${external.join(", ")}`).toEqual([]);
  });

  test("mounts no authentication provider", async ({ page }) => {
    const authCalls: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/auth/")) authCalls.push(request.url());
    });

    await page.goto("/terms", { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    expect(authCalls).toEqual([]);
  });

  test("renders with JavaScript disabled", async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();

    const response = await page.goto("/terms");
    expect(response?.status()).toBe(200);

    // Server-rendered, so the substance is in the HTML rather than assembled
    // by a script the reader may not be running.
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Terms of Service");
    await expect(page.getByText(COMPANY).first()).toBeVisible();
    await expect(page.getByText(REGISTERED_OFFICE).first()).toBeVisible();

    await context.close();
  });

  test("sets no cookie and writes nothing to browser storage", async ({ page, context }) => {
    await page.goto("/terms", { waitUntil: "networkidle" });

    expect(await context.cookies()).toEqual([]);
    const stored = await page.evaluate(() => {
      const dump = (store: Storage) => Object.keys(store).join(",");
      return `${dump(localStorage)}|${dump(sessionStorage)}`;
    });
    expect(stored).toBe("|");
  });

  test("still carries the nonce-based CSP", async ({ page }) => {
    const response = await page.goto("/terms");
    const header = response?.headers()["content-security-policy"] ?? "";

    expect(header).toContain("'nonce-");
    expect(header).not.toContain("script-src 'self' 'unsafe-inline'");
    expect(header).toContain("frame-ancestors 'none'");
  });

  test("produces no console error", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(String(error)));

    await page.goto("/terms", { waitUntil: "networkidle" });

    expect(errors).toEqual([]);
  });
});

test.describe("Terms page — statutory disclosures", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/terms");
  });

  test("names the contracting company, number and registered office", async ({ page }) => {
    const body = await page.locator("body").innerText();

    // Regulation 25 of the Trading Disclosures Regulations 2015 and regulation
    // 6 of the E-Commerce Regulations 2002.
    expect(body).toContain(COMPANY);
    expect(body).toContain(COMPANY_NUMBER);
    expect(body).toContain(REGISTERED_OFFICE);
    expect(body).toContain("England and Wales");
    expect(body).toContain("private limited company");
    expect(body).toContain(SUPPORT_CONTACT);
    expect(body).toContain(PRIVACY_CONTACT);
  });

  test("keeps the registered office exactly as Companies House holds it", async ({ page }) => {
    // The repeated "Eton Road" is the register's own wording. Tidying it would
    // make the disclosure disagree with the official record.
    const body = await page.locator("body").innerText();
    expect(body).toContain("200 Eton Road Eton Road");
  });

  test("does not name an individual as the contracting party", async ({ page }) => {
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("Adnan");
  });

  test("shows the version and marks it as a draft", async ({ page }) => {
    await expect(page.getByTestId("terms-version")).toHaveText(/^draft-/);
    await expect(page.getByTestId("terms-draft-banner")).toBeVisible();
    await expect(page.getByTestId("terms-draft-banner")).toContainText(/pending legal review/i);
  });

  test("links to the privacy notice and back", async ({ page }) => {
    await page.getByRole("navigation", { name: "Legal documents" }).first()
      .getByRole("link", { name: "Privacy Notice" })
      .click();

    await expect(page).toHaveURL(/\/privacy$/);
    await page.getByRole("link", { name: "Terms of Service" }).first().click();
    await expect(page).toHaveURL(/\/terms$/);
  });
});

test.describe("Terms page — contact separation", () => {
  /**
   * Two addresses doing two different jobs. The failure mode this guards is not
   * a broken page but a silently misrouted one: a cancellation landing in a
   * data-protection inbox, or a data-subject request landing in a support queue
   * where the statutory clock is not being watched. A swap would look perfectly
   * fine on screen, which is exactly why it needs pinning.
   */
  test.beforeEach(async ({ page }) => {
    await page.goto("/terms");
  });

  test("cancellation is directed to the support address", async ({ page }) => {
    const section = page.locator("#cancellation").locator("xpath=..");

    await expect(section).toContainText(SUPPORT_CONTACT);
    await expect(section).not.toContainText(PRIVACY_CONTACT);
  });

  test("contractual notices are directed to the support address", async ({ page }) => {
    const section = page.locator("#notices").locator("xpath=..");

    await expect(section).toContainText(SUPPORT_CONTACT);
    await expect(section).not.toContainText(PRIVACY_CONTACT);
  });

  test("general complaints go to support, privacy complaints to privacy", async ({
    page,
  }) => {
    const section = page.locator("#complaints").locator("xpath=..");

    await expect(section).toContainText(SUPPORT_CONTACT);
    await expect(section).toContainText(PRIVACY_CONTACT);
    // The privacy route is labelled, not left for the reader to infer.
    await expect(section).toContainText(/complaint about privacy/i);
  });

  test("a data processing agreement request goes to the privacy address", async ({
    page,
  }) => {
    const section = page.locator("#data-roles").locator("xpath=..");

    await expect(section).toContainText(PRIVACY_CONTACT);
    await expect(section).not.toContainText(SUPPORT_CONTACT);
  });

  test("closure goes to support and personal-data requests go to privacy", async ({
    page,
  }) => {
    const section = page.locator("#closure").locator("xpath=..");

    await expect(section).toContainText(SUPPORT_CONTACT);
    await expect(section).toContainText(PRIVACY_CONTACT);
    await expect(section).toContainText(/To close an account, write to/i);
    await expect(section).toContainText(/copy of personal data, or its deletion/i);
  });

  test("the shared disclosure block labels both addresses", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/Support and contractual notices/i);
    expect(body).toMatch(/Privacy and data protection/i);

    // Each label must sit with its own address, not the other one.
    const disclosure = await page
      .locator("dl")
      .first()
      .innerText();
    const supportIndex = disclosure.indexOf("Support and contractual notices");
    const privacyIndex = disclosure.indexOf("Privacy and data protection");
    expect(supportIndex).toBeGreaterThanOrEqual(0);
    expect(privacyIndex).toBeGreaterThan(supportIndex);
    expect(disclosure.slice(supportIndex, privacyIndex)).toContain(SUPPORT_CONTACT);
    expect(disclosure.slice(privacyIndex)).toContain(PRIVACY_CONTACT);
  });

  test("the addresses are not swapped anywhere on the page", async ({ page }) => {
    // Every mailto on the page must be one of the two, and the privacy address
    // must never appear in a sentence about cancelling or serving notice.
    const hrefs = await page.locator('a[href^="mailto:"]').evaluateAll((links) =>
      links.map((link) => link.getAttribute("href") ?? ""),
    );
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect([`mailto:${SUPPORT_CONTACT}`, `mailto:${PRIVACY_CONTACT}`]).toContain(href);
    }

    const cancellation = await page.locator("#cancellation").locator("xpath=..").innerText();
    expect(cancellation).not.toContain(PRIVACY_CONTACT);
  });

  test("makes no claim that either mailbox is operational or tested", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).not.toMatch(/monitored 24|staffed|manned inbox|we respond within/i);
    expect(body).not.toMatch(/mailbox (is|has been) (tested|verified)/i);
    // The honest position about response times is still there.
    expect(body).toMatch(/do not publish a\s+guaranteed response time/i);
  });
});

test.describe("Terms page — commercial positions", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/terms");
  });

  test("states the B2B-only, 18+ and authority position", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/only to businesses/i);
    expect(body).toMatch(/at least 18/i);
    expect(body).toMatch(/authorised to bind/i);
    expect(body).toMatch(/not offered to consumers/i);
  });

  test("states the billing, cancellation and refund position", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/monthly or annually/i);
    expect(body).toMatch(/renews automatically/i);
    expect(body).toMatch(/exclusive of VAT/i);
    expect(body).toMatch(/cancel at any time/i);
    expect(body).toMatch(/end of the period you have already paid for/i);
    expect(body).toMatch(/not refunded pro rata/i);
    expect(body).toMatch(/required by law/i);
  });

  test("preserves the liabilities that cannot lawfully be excluded", async ({ page }) => {
    const body = await page.locator("body").innerText();

    // UCTA 1977 s.2(1) and the fraud carve-out.
    expect(body).toMatch(/death or personal injury caused by our negligence/i);
    expect(body).toMatch(/fraud or fraudulent misrepresentation/i);
    expect(body).toMatch(/cannot lawfully be limited or excluded/i);
  });

  test("states the aggregate cap and the no-fees fallback", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/twelve months before the event giving rise to the claim/i);
    expect(body).toMatch(/If you have paid no Fees/i);
    expect(body).toContain("£100");
  });

  test("disclaims affiliation with the marketplaces", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/not affiliated with, endorsed by, sponsored by or partnered with/i);
    for (const platform of ["Shopify", "eBay", "AliExpress", "TikTok", "Google"]) {
      expect(body).toContain(platform);
    }
  });

  test("makes the merchant responsible for compliance, tax and their own customers", async ({
    page,
  }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/rules of every marketplace/i);
    expect(body).toMatch(/legality, safety, labelling/i);
    expect(body).toMatch(/intellectual-property rights/i);
    expect(body).toMatch(/taxes, VAT, duties and customs/i);
    expect(body).toMatch(/returns, refunds and consumer-law obligations/i);
  });

  test("excludes third-party rights under the 1999 Act", async ({ page }) => {
    const body = await page.locator("body").innerText();
    expect(body).toMatch(/Contracts \(Rights of Third Parties\) Act 1999/);
  });

  test("names England and Wales as the governing law and forum", async ({ page }) => {
    const body = await page.locator("body").innerText();
    expect(body).toMatch(/governed by the law of England and Wales/i);
    expect(body).toMatch(/courts of England and Wales have exclusive jurisdiction/i);
  });
});

test.describe("Terms page — promises the product cannot keep", () => {
  /**
   * The audit found no billing system, no backups, no export feature, no
   * self-service cancellation and no SLA. Each of those is an easy sentence to
   * add and an expensive one to have written, so each is pinned as an absence.
   */
  test.beforeEach(async ({ page }) => {
    await page.goto("/terms");
  });

  test("promises no uptime, SLA or guaranteed availability", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/do not offer a service-level agreement/i);
    expect(body).toMatch(/do not commit to any level of uptime/i);
    expect(body).not.toMatch(/99\.9|99\.99|uptime guarantee/i);
  });

  test("states plainly that there are no backups", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/do not currently operate a backup service/i);
    expect(body).not.toMatch(/backed up (daily|nightly|regularly)/i);
  });

  test("does not claim a data export feature that does not exist", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/no self-service data export/i);
  });

  test("does not invent a free trial", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/do not currently offer a free trial/i);
    expect(body).not.toMatch(/start your (free )?trial|14[- ]day|30[- ]day free/i);
  });

  test("claims no certification, ICO registration or executed DPA", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).not.toMatch(/ISO ?27001|SOC ?2|PCI[- ]DSS/i);
    expect(body).not.toMatch(/registered with the (UK )?Information Commissioner/i);
    expect(body).not.toMatch(/we have signed a data processing agreement/i);
    // The honest position instead.
    expect(body).toMatch(/have not yet signed a data processing agreement/i);
  });

  test("guarantees no commercial outcome", async ({ page }) => {
    const body = await page.locator("body").innerText();

    expect(body).toMatch(/do not guarantee sales, revenue, profit/i);
    expect(body).not.toMatch(/guaranteed (sales|profit|ranking)/i);
  });

  test("does not claim to be legally approved", async ({ page }) => {
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/approved by (our )?(solicitors?|lawyers?|counsel)/i);
    expect(body).not.toMatch(/legal advice/i);
  });
});

test.describe("Terms page — presentation", () => {
  test("uses one h1 and semantic section headings", async ({ page }) => {
    await page.goto("/terms");

    await expect(page.locator("h1")).toHaveCount(1);
    expect(await page.locator("h2").count()).toBeGreaterThanOrEqual(30);
    expect(await page.locator("section[aria-labelledby]").count()).toBeGreaterThanOrEqual(30);
  });

  test("links are reachable and focusable by keyboard", async ({ page }) => {
    await page.goto("/terms");

    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    expect(focused).toBe("A");
  });

  for (const [name, width, height] of [
    ["mobile", 375, 812],
    ["tablet", 768, 1024],
    ["desktop", 1440, 900],
  ] as const) {
    test(`does not overflow horizontally at ${name}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await page.goto("/terms");

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      );
      expect(overflow).toBe(false);
    });
  }

  for (const scheme of ["light", "dark"] as const) {
    test(`renders in ${scheme} mode without a session`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: scheme });
      const page = await context.newPage();

      const authCalls: string[] = [];
      page.on("request", (request) => {
        if (request.url().includes("/auth/")) authCalls.push(request.url());
      });

      await page.goto("/terms", { waitUntil: "networkidle" });

      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("html")).toHaveClass(new RegExp(scheme));
      expect(authCalls).toEqual([]);

      await context.close();
    });
  }
});

test.describe("Registration acceptance", () => {
  test("the acceptance box is not pre-ticked", async ({ page }) => {
    await page.goto("/register");

    await expect(page.getByTestId("accept-legal")).not.toBeChecked();
  });

  test("the acceptance label links to both documents", async ({ page }) => {
    await page.goto("/register");

    const label = page.locator('label[for="accept-legal"]');
    await expect(label.getByRole("link", { name: "Terms of Service" })).toHaveAttribute(
      "href",
      "/terms",
    );
    await expect(label.getByRole("link", { name: "Privacy Notice" })).toHaveAttribute(
      "href",
      "/privacy",
    );
  });

  test("no longer tells the reader the Terms are unpublished", async ({ page }) => {
    await page.goto("/register");

    const label = await page.locator('label[for="accept-legal"]').innerText();
    expect(label).not.toMatch(/not yet published/i);
  });

  test("the sign-in footer carries both documents and the company disclosures", async ({
    page,
  }) => {
    await page.goto("/login");

    const footer = page.locator("footer");
    await expect(footer.getByRole("link", { name: "Terms of Service" })).toBeVisible();
    await expect(footer.getByRole("link", { name: "Privacy Policy" })).toBeVisible();
    await expect(footer).toContainText(COMPANY);
    await expect(footer).toContainText(COMPANY_NUMBER);
    await expect(footer).toContainText(REGISTERED_OFFICE);
  });
});
