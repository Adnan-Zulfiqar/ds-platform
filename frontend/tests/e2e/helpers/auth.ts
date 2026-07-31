import type { Page } from "@playwright/test";

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
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Meets the server-side password policy: 12+ chars, mixed case, a digit. */
export const TEST_PASSWORD = "Correct-Horse-Battery9";

export interface TestAccount {
  email: string;
  password: string;
  companyName: string;
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
  try {
    const response = await fetch(`${API_URL}/health/live`, {
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch {
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
  try {
    const response = await fetch(`${API_URL}/health`, {
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
  } catch {
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
  await page.goto("/register");

  await page.getByLabel("Company name").fill(account.companyName);
  await page.getByLabel("Work email").fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByLabel("Confirm password").fill(account.password);

  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 20_000 });

  return account;
}
