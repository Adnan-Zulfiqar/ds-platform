import type { Page } from "@playwright/test";

import { PRIVACY_NOTICE_VERSION, TERMS_VERSION } from "@/lib/legal";

/**
 * Helpers for tests that need a signed-in session.
 *
 * Accounts are created through the **real API**, not by stubbing the network.
 * A mocked session would test the mock: it would keep passing after a change to
 * token handling, cookie attributes, or the shape of `/auth/me` — exactly the
 * wiring these tests exist to cover.
 *
 * The cost is that these tests need the backend and its database running. They
 * skip cleanly when it is not, rather than failing with a wall of connection
 * errors that hide real regressions.
 *
 * **Isolation (UX-L2A-R6):** the API origin is an allowlist of parsed `URL`
 * values — loopback `127.0.0.1` on ports `8100–8199` only — not a denylist of
 * string literals. Empty or production-like URLs fail before any request.
 * GitHub Actions may use `http://127.0.0.1:8000` only when `CI`,
 * `GITHUB_ACTIONS`, and `E2E_ALLOW_LOCAL_8000` are all exactly set.
 */

/** Configuration / isolation failure — must not be treated as “API down”. */
export class E2eIsolationError extends Error {
  override readonly name = "E2eIsolationError";
}

const ISOLATED_PORT_MIN = 8100;
const ISOLATED_PORT_MAX = 8199;

let hasLoggedApiOrigin = false;
let lastLoggedApiOrigin: string | undefined;

/** Test-only: reset the once-per-process origin diagnostic. */
export function resetE2eApiOriginLogForTests(): void {
  hasLoggedApiOrigin = false;
  lastLoggedApiOrigin = undefined;
}

/** Test-only: inspect the once-per-process origin diagnostic. */
export function peekE2eApiOriginLogForTests(): {
  logged: boolean;
  origin: string | undefined;
} {
  return { logged: hasLoggedApiOrigin, origin: lastLoggedApiOrigin };
}

function refuse(message: string): never {
  throw new E2eIsolationError(message);
}

function hasCiEphemeral8000Proof(env: NodeJS.ProcessEnv): boolean {
  return (
    env.CI === "true" &&
    env.GITHUB_ACTIONS === "true" &&
    env.E2E_ALLOW_LOCAL_8000 === "1"
  );
}

function isBarePath(pathname: string): boolean {
  return pathname === "" || pathname === "/";
}

function isExactCiEphemeralOrigin(url: URL): boolean {
  return (
    url.protocol === "http:" &&
    url.hostname === "127.0.0.1" &&
    url.port === "8000" &&
    isBarePath(url.pathname) &&
    url.username === "" &&
    url.password === "" &&
    url.search === "" &&
    url.hash === ""
  );
}

function isIsolatedLoopbackOrigin(url: URL): boolean {
  if (url.protocol !== "http:") return false;
  if (url.hostname !== "127.0.0.1") return false;
  if (url.port === "") return false;
  const port = Number(url.port);
  if (!Number.isInteger(port) || port < ISOLATED_PORT_MIN || port > ISOLATED_PORT_MAX) {
    return false;
  }
  if (!isBarePath(url.pathname)) return false;
  if (url.username !== "" || url.password !== "") return false;
  if (url.search !== "" || url.hash !== "") return false;
  return true;
}

function logApiOriginOnce(origin: string): void {
  if (hasLoggedApiOrigin) return;
  hasLoggedApiOrigin = true;
  lastLoggedApiOrigin = origin;
  // Node-side diagnostic only — never written into the browser.
  console.log(`[E2E] API origin: ${origin}`);
}

/**
 * Resolve the API base for live e2e helpers.
 *
 * Uses the WHATWG `URL` parser. Throws {@link E2eIsolationError} rather than
 * falling back to a production-port default. Does not perform DNS or I/O.
 */
export function resolveE2eApiUrl(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const raw = (env.NEXT_PUBLIC_API_URL ?? env.E2E_API_URL ?? "").trim();
  if (!raw) {
    refuse(
      "E2E isolation: set NEXT_PUBLIC_API_URL (or E2E_API_URL) explicitly. " +
        "Refusing any default that could reach a production-like :8000 host.",
    );
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    refuse(`E2E isolation: refusing invalid API URL "${raw}".`);
  }

  if (isIsolatedLoopbackOrigin(parsed)) {
    const origin = `http://127.0.0.1:${parsed.port}`;
    logApiOriginOnce(origin);
    return origin;
  }

  if (isExactCiEphemeralOrigin(parsed)) {
    if (!hasCiEphemeral8000Proof(env)) {
      refuse(
        "E2E isolation: http://127.0.0.1:8000 requires CI=true, " +
          "GITHUB_ACTIONS=true, and E2E_ALLOW_LOCAL_8000=1 together. " +
          "Local runs must use http://127.0.0.1:8100–8199.",
      );
    }
    const origin = "http://127.0.0.1:8000";
    logApiOriginOnce(origin);
    return origin;
  }

  refuse(
    `E2E isolation: refusing API URL "${raw}". Allowed: ` +
      `http://127.0.0.1:8100–8199 (no path/query/userinfo), or in GitHub Actions ` +
      `only http://127.0.0.1:8000 with CI + GITHUB_ACTIONS + E2E_ALLOW_LOCAL_8000.`,
  );
}

/**
 * Lazy so hermetic specs can import sibling helpers without requiring an API
 * URL until a live call actually runs.
 */
export function getApiUrl(): string {
  return resolveE2eApiUrl();
}

/** Resolved on first string coercion / live helper call — never a :8000 default. */
export const API_URL = {
  toString: () => resolveE2eApiUrl(),
  valueOf: () => resolveE2eApiUrl(),
  [Symbol.toPrimitive]: () => resolveE2eApiUrl(),
} as unknown as string;

/** Meets the server-side password policy: 12+ chars, mixed case, a digit. */
export const TEST_PASSWORD = "Correct-Horse-Battery9";

/**
 * The acceptance a signup must carry.
 *
 * Registration records which documents the account holder agreed to, so the API
 * refuses a request without them, and refuses one quoting a version that is not
 * the server's current one.
 *
 * **Imported rather than transcribed.** These were literals here until the
 * Terms version changed from `"unpublished"` to a draft identifier, at which
 * point every API-created account in the suite was refused — correctly, by a
 * server doing exactly what it is supposed to do, against a helper nobody had
 * remembered to update. Reading `lib/legal.ts` means the copy cannot drift
 * again; the backend remains the authority, and a mismatch between the two
 * still fails loudly.
 */
export const LEGAL_ACCEPTANCE_BODY = {
  termsAccepted: true,
  privacyAccepted: true,
  termsVersion: TERMS_VERSION,
  privacyVersion: PRIVACY_NOTICE_VERSION,
} as const;

export interface TestAccount {
  email: string;
  password: string;
  companyName: string;
}

/**
 * Stop the page from fetching Google's real sign-in script.
 *
 * `/login` and `/register` both mount the Google button, so *every* test that
 * signs in through the UI was making a live request to accounts.google.com and
 * getting a 403 back — the E2E client id is synthetic, as it must be. That is
 * an outbound call to a third party from a test run, and the 403s and
 * `[GSI_LOGGER]` output landed in the console-error assertions of unrelated
 * specs.
 *
 * Served as an empty script rather than aborted. Both stop the real thing from
 * loading, but an abort raises `net::ERR_FAILED` in the console, which is the
 * same noise in a different costume — and one of these specs asserts the
 * console is clean. An empty body loads successfully, leaves `window.google`
 * undefined, and the component settles on "Google sign-in unavailable" and
 * renders nothing. Specs that are *about* the button stub this URL with a fake
 * implementation instead.
 */
export async function blockGoogleIdentityScript(page: Page): Promise<void> {
  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: "" }),
  );
}

/**
 * Tick the legal-acceptance box on the registration form.
 *
 * Not a bare `.check()`. The form validates on blur, so clicking the box
 * straight after typing in the last field fires that validation, a message
 * appears above the box, and the box shifts ~28px out from under the pointer
 * mid-click — the click lands on nothing and the state never changes. Blurring
 * first lets the message render and the layout settle, so the click hits what
 * it aimed at.
 */
export async function acceptLegal(page: Page): Promise<void> {
  // Nothing may be focused, which is fine — there is then nothing to settle.
  await page
    .locator(":focus")
    .blur({ timeout: 1000 })
    .catch(() => undefined);
  const box = page.getByTestId("accept-legal");
  await box.waitFor({ state: "visible" });
  await box.check();
}

/** A unique account per test, so tests never contend over one fixture user. */
export function buildAccount(): TestAccount {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return {
    email: `e2e-${suffix}@example.com`,
    password: TEST_PASSWORD,
    companyName: `E2E Test Co ${suffix.slice(-6)}`,
  };
}

/** Whether the backend is reachable, used to skip rather than fail. */
export async function isApiReachable(): Promise<boolean> {
  // Validate before the network try/catch so isolation errors never look like
  // a quiet “API not reachable” skip (UX-L2A-R6 F-4).
  const base = resolveE2eApiUrl();
  try {
    const response = await fetch(`${base}/health/live`, {
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch (error) {
    if (error instanceof E2eIsolationError) throw error;
    return false;
  }
}

/**
 * Whether Redis is available to the backend.
 *
 * Some flows need it and correctly refuse without it. Starting an OAuth
 * connection is one: the `state` token is stored in Redis, and it is the CSRF
 * defence for the redirect. With nowhere to store it the server fails closed
 * rather than beginning a flow it could not verify on return — so those tests
 * are skipped, not failed, when Redis is absent.
 *
 * Read from the health endpoint, which already reports component status, rather
 * than by connecting to Redis from the test.
 */
export async function isRedisAvailable(): Promise<boolean> {
  const base = resolveE2eApiUrl();
  try {
    const response = await fetch(`${base}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok && response.status !== 503) return false;

    const body = (await response.json()) as {
      components?: { name: string; status: string }[];
    };
    return (
      body.components?.some(
        (component) => component.name === "redis" && component.status === "healthy",
      ) ?? false
    );
  } catch (error) {
    if (error instanceof E2eIsolationError) throw error;
    return false;
  }
}

/**
 * Register through the UI and land on the dashboard.
 *
 * Driving the real form rather than calling the API and injecting a token: the
 * access token lives in memory inside the app, so there is nowhere to inject it
 * from outside. Going through the form is both simpler and more faithful.
 */
export async function registerAndSignIn(
  page: Page,
  account: TestAccount = buildAccount(),
): Promise<TestAccount> {
  await blockGoogleIdentityScript(page);

  // Rate-limit 429s under a busy suite look like navigation flakes (M13). Retry
  // with a fresh account rather than failing the whole test on a shared IP bucket.
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const candidate = attempt === 0 ? account : buildAccount();
    await page.goto("/register");

    await page.getByLabel("Company name").fill(candidate.companyName);
    await page.getByLabel("Work email").fill(candidate.email);
    await page.getByLabel("Password", { exact: true }).fill(candidate.password);
    await page.getByLabel("Confirm password").fill(candidate.password);
    // The submit button is disabled until this is ticked, and the flag it sets
    // is what the server records. Registering without it is meant to be
    // impossible, so the test does what a person does.
    await acceptLegal(page);

    await page.getByRole("button", { name: "Create account" }).click();
    try {
      await page.waitForURL(/\/dashboard/, { timeout: 20_000 });
      return candidate;
    } catch {
      if (page.isClosed()) {
        throw new Error("Registration page closed before reaching /dashboard");
      }
      await page.waitForTimeout(1500 * (attempt + 1));
    }
  }

  throw new Error("Registration did not reach /dashboard after retries");
}

/**
 * Register via the API, then sign in through the login form.
 *
 * Avoids flaky full-page registration UI under rate limits while still proving
 * the cookie/token path the SPA uses after a real login.
 */
export async function registerViaApiAndSignIn(
  page: Page,
  account: TestAccount = buildAccount(),
): Promise<TestAccount> {
  await blockGoogleIdentityScript(page);

  let lastStatus = 0;
  let lastBody = "";
  let candidate = account;

  for (let attempt = 0; attempt < 6; attempt += 1) {
    candidate = attempt === 0 ? account : buildAccount();
    const response = await page.request.post(`${API_URL}/api/v1/auth/register`, {
      data: {
        companyName: candidate.companyName,
        email: candidate.email,
        password: candidate.password,
        firstName: "E2E",
        lastName: "Operator",
        ...LEGAL_ACCEPTANCE_BODY,
      },
    });

    if (response.ok()) {
      await page.goto("/login");
      await page.getByLabel("Email").fill(candidate.email);
      await page.getByLabel("Password", { exact: true }).fill(candidate.password);
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
      return candidate;
    }

    lastStatus = response.status();
    lastBody = await response.text();
    if (lastStatus !== 429) {
      break;
    }
    await page.waitForTimeout(1500 * (attempt + 1));
  }

  throw new Error(`API registration failed (${lastStatus}): ${lastBody}`);
}
