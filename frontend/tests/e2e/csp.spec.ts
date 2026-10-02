import { expect, test, type Page } from "@playwright/test";

import { isApiReachable } from "./helpers/auth";

/**
 * AUTH-G1-R2 — the Content-Security-Policy, checked in a browser.
 *
 * The header can be read from the configuration source and look correct while
 * the application is broken or unprotected, so none of this reads the source.
 * Every assertion below is against a real response from the production build
 * and a real Chromium enforcing it.
 *
 * Two failures are being guarded against, and they pull in opposite directions:
 *
 * * A policy that still permits inline script. `'unsafe-inline'` in
 *   `script-src` allows precisely what an XSS payload needs, which makes the
 *   directive decorative.
 * * A policy so tight the application cannot run. Next.js emits inline scripts
 *   of its own — the hydration bootstrap and the streamed payload — so a policy
 *   that simply banned them would produce a blank, un-hydrated page.
 *
 * The nonce is what resolves them, and only a browser can show that it does.
 *
 * **No request reaches Google.** `accounts.google.com` is intercepted in every
 * test here, and one of them asserts that nothing escaped.
 */

const GSI_URL = "https://accounts.google.com/gsi/client";

/** Replace Google's script with one that draws a button. */
async function stubGoogleScript(page: Page): Promise<void> {
  await page.route(`https://accounts.google.com/**`, (route) => {
    if (route.request().url().startsWith(GSI_URL)) {
      return route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: `
          window.google = { accounts: { id: {
            initialize: (config) => { window.__gsiConfig = config; },
            renderButton: (parent) => {
              const b = document.createElement('button');
              b.type = 'button';
              b.setAttribute('data-testid', 'stub-google-button');
              b.textContent = 'Continue with Google';
              parent.appendChild(b);
            },
          } } };
        `,
      });
    }
    // Anything else Google would have asked for — the stylesheet, telemetry —
    // is answered emptily rather than allowed out.
    return route.fulfill({ status: 200, contentType: "text/plain", body: "" });
  });
}

/** Console messages that are Content-Security-Policy refusals. */
function cspViolations(messages: string[]): string[] {
  return messages.filter((message) => /Content Security Policy/i.test(message));
}

function parseDirectives(header: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const part of header.split(";")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const space = trimmed.indexOf(" ");
    map.set(
      space === -1 ? trimmed : trimmed.slice(0, space),
      space === -1 ? "" : trimmed.slice(space + 1),
    );
  }
  return map;
}

test.describe("Content-Security-Policy header", () => {
  test("script-src carries a nonce and no 'unsafe-inline'", async ({ page }) => {
    const response = await page.goto("/login");
    const header = response?.headers()["content-security-policy"];
    expect(header, "no CSP header on the response").toBeTruthy();

    const directives = parseDirectives(header ?? "");
    const scriptSrc = directives.get("script-src") ?? "";

    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(scriptSrc).toMatch(/'nonce-[A-Za-z0-9+/=]{16,}'/);
    expect(scriptSrc).toContain("'self'");
    expect(scriptSrc).toContain("https://accounts.google.com");
    // Production (start:e2e) must never include unsafe-eval. A reused next-dev
    // server will fail this on purpose — point E2E_BASE_URL at standalone.
    expect(scriptSrc, "production CSP must not contain unsafe-eval").not.toContain(
      "'unsafe-eval'",
    );
  });

  test("no directive opens a wildcard or a whole-of-Google origin", async ({ page }) => {
    const response = await page.goto("/login");
    const header = response?.headers()["content-security-policy"] ?? "";

    // A wildcard host, or one covering user-content properties, would undo the
    // point of naming origins at all.
    expect(header).not.toMatch(/https:\/\/\*\.google\.com/);
    expect(header).not.toMatch(/(^|[; ])(script|frame|connect|font)-src[^;]*\s\*(\s|;|$)/);
    expect(header).toContain("object-src 'none'");
    expect(header).toContain("frame-ancestors 'none'");
    expect(header).toContain("base-uri 'self'");
  });

  test("every response gets its own nonce", async ({ page }) => {
    // A nonce reused across responses is published in the HTML of every page,
    // so an attacker reads it once and attaches it to their own script.
    const nonces = new Set<string>();
    for (let index = 0; index < 3; index += 1) {
      const response = await page.goto(`/login?cache-bust=${index}`);
      const header = response?.headers()["content-security-policy"] ?? "";
      const match = /'nonce-([A-Za-z0-9+/=]+)'/.exec(header);
      expect(match, "no nonce in script-src").toBeTruthy();
      nonces.add(match?.[1] ?? "");
    }

    expect(nonces.size).toBe(3);
  });

  test("the nonce is not in the URL, a cookie, or browser storage", async ({ page }) => {
    const response = await page.goto("/login");
    const header = response?.headers()["content-security-policy"] ?? "";
    const nonce = /'nonce-([A-Za-z0-9+/=]+)'/.exec(header)?.[1] ?? "";
    expect(nonce).toBeTruthy();

    expect(page.url()).not.toContain(nonce);

    const cookies = await page.context().cookies();
    expect(cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("|")).not.toContain(
      nonce,
    );

    const stored = await page.evaluate(() => {
      const dump = (store: Storage) =>
        Object.keys(store)
          .map((key) => `${key}=${store.getItem(key)}`)
          .join("|");
      return `${dump(localStorage)}||${dump(sessionStorage)}`;
    });
    expect(stored).not.toContain(nonce);
  });
});

test.describe("The policy the browser actually enforces", () => {
  test.beforeEach(async ({ page }) => {
    await stubGoogleScript(page);
  });

  test("an inline script without the nonce does not run", async ({ page }) => {
    await page.goto("/login");

    await page.evaluate(() => {
      const script = document.createElement("script");
      script.textContent = "window.__INJECTED_RAN__ = true;";
      document.head.appendChild(script);
    });
    await page.waitForTimeout(500);

    // This is the XSS payload. Under `'unsafe-inline'` it would have run.
    expect(await page.evaluate(() => "__INJECTED_RAN__" in window)).toBe(false);
  });

  test("an inline script carrying a made-up nonce does not run", async ({ page }) => {
    await page.goto("/login");

    await page.evaluate(() => {
      const script = document.createElement("script");
      // An attacker who cannot read the response header has to guess.
      script.setAttribute("nonce", "AAAAAAAAAAAAAAAAAAAAAA==");
      script.textContent = "window.__GUESSED_NONCE_RAN__ = true;";
      document.head.appendChild(script);
    });
    await page.waitForTimeout(500);

    expect(await page.evaluate(() => "__GUESSED_NONCE_RAN__" in window)).toBe(false);
  });

  test("Next.js hydrates, and the theme script runs", async ({ page }) => {
    const violations: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") violations.push(message.text());
    });

    await page.goto("/login");

    // Hydration: a control that only works once React has attached.
    const email = page.getByLabel("Email");
    await email.fill("someone@example.com");
    await expect(email).toHaveValue("someone@example.com");

    // `next-themes` writes its class from an inline script before React runs.
    // Without a nonce of its own it would be blocked and every reload would
    // flash the wrong theme.
    await expect(page.locator("html")).toHaveClass(/light|dark/);

    expect(cspViolations(violations)).toEqual([]);
  });

  test("every script tag in the delivered HTML carries the response nonce", async ({
    page,
  }) => {
    const response = await page.goto("/login");
    const header = response?.headers()["content-security-policy"] ?? "";
    const nonce = /'nonce-([A-Za-z0-9+/=]+)'/.exec(header)?.[1] ?? "";
    const html = (await response?.text()) ?? "";

    const tags = html.match(/<script[^>]*>/g) ?? [];
    expect(tags.length).toBeGreaterThan(0);

    const unnonced = tags.filter((tag) => !tag.includes(`nonce="${nonce}"`));
    expect(unnonced, `script tags without the response nonce: ${unnonced.join(" ")}`).toEqual(
      [],
    );
  });

  for (const route of ["/login", "/register", "/privacy"]) {
    test(`${route} renders with no CSP violation in the console`, async ({ page }) => {
      test.skip(!(await isApiReachable()), "Backend API is not reachable.");

      const violations: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") violations.push(message.text());
      });

      await page.goto(route, { waitUntil: "networkidle" });

      expect(cspViolations(violations)).toEqual([]);
    });
  }

  test("the Google button still loads and draws under the policy", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    const violations: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") violations.push(message.text());
    });

    await page.goto("/login");

    // The stub is served from accounts.google.com, so this proves the origin is
    // permitted by `script-src` rather than merely written in the header.
    await expect(page.getByTestId("stub-google-button")).toBeVisible({ timeout: 15000 });
    expect(cspViolations(violations)).toEqual([]);
  });

  test("no request escapes to Google", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    // The stub in `beforeEach` answers every accounts.google.com request. This
    // records what the browser asked for and what was intercepted, so a request
    // that reached the network would appear in one list and not the other.
    const asked: string[] = [];
    const intercepted: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("google.com")) asked.push(request.url());
    });
    await page.route("https://accounts.google.com/**", async (route) => {
      intercepted.push(route.request().url());
      await route.fallback();
    });

    await page.goto("/login");
    await expect(page.getByTestId("stub-google-button")).toBeVisible({ timeout: 15000 });

    expect(asked.length).toBeGreaterThan(0);
    for (const url of asked) {
      expect(intercepted, `${url} was not intercepted`).toContain(url);
    }
  });
});

/**
 * The routing gate in `proxy.ts` (Next.js 16's name for middleware). These
 * pin its two non-CSP contracts across the rename and the move to the
 * Node.js runtime.
 */
test.describe("Proxy routing gate", () => {
  test("protected pages are never cacheable", async ({ request }) => {
    // (Public pages are no-store too: the per-request nonce makes every page
    // dynamic, and Next.js marks dynamic responses no-store itself.)
    const protectedPage = await request.get("/dashboard", { maxRedirects: 0 });
    expect(protectedPage.headers()["cache-control"]).toContain("no-store");
  });

  test("a client-supplied nonce header is replaced, not trusted", async ({ request }) => {
    const chosen = "QUFBQUFBQUFBQUFBQUFBQQ==";
    const response = await request.get("/login", { headers: { "x-nonce": chosen } });
    const header = response.headers()["content-security-policy"] ?? "";
    expect(header).toMatch(/'nonce-[A-Za-z0-9+/=]{16,}'/);
    expect(header).not.toContain(chosen);
  });
});
