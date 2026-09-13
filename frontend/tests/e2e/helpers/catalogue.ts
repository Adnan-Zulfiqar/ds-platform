import type { APIRequestContext } from "@playwright/test";

import {
  API_URL,
  LEGAL_ACCEPTANCE_BODY,
  TEST_PASSWORD,
  type TestAccount,
  buildAccount,
} from "./auth";

/** Product id from the captured AliExpress fixture used in backend integration tests. */
export const FIXTURE_PRODUCT_ID = "3256806389000685";

interface AuthTokens {
  accessToken: string;
}

interface RegisterResponse {
  identity: {
    tenant: { id: string };
    user: { email: string };
  };
  tokens: AuthTokens;
}

/**
 * Register a tenant through the API and return its bearer token.
 *
 * Playwright cannot inject the in-memory access token the UI stores after
 * registration, but it can seed catalogue state through the same endpoints the
 * UI calls — which is what makes the import-and-view flow testable without
 * stubbing the browser network.
 */
export async function registerViaApi(
  request: APIRequestContext,
  account: TestAccount = buildAccount(),
): Promise<{ account: TestAccount; accessToken: string; tenantId: string }> {
  // Parallel Playwright runs burn through the global IP rate limit quickly.
  // Backing off on 429 is cheaper than disabling the limiter for the whole suite.
  let lastStatus = 0;
  let lastBody = "";
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const response = await request.post(`${API_URL}/api/v1/auth/register`, {
      data: {
        companyName: account.companyName,
        email: account.email,
        password: account.password,
        firstName: "E2E",
        lastName: "Operator",
        ...LEGAL_ACCEPTANCE_BODY,
      },
    });

    if (response.ok()) {
      const body = (await response.json()) as RegisterResponse;
      return {
        account,
        accessToken: body.tokens.accessToken,
        tenantId: body.identity.tenant.id,
      };
    }

    lastStatus = response.status();
    lastBody = await response.text();
    if (lastStatus !== 429) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500 * (attempt + 1)));
  }

  throw new Error(`Registration failed (${lastStatus}): ${lastBody}`);
}

/**
 * Complete the AliExpress OAuth callback using the platform application credentials.
 *
 * Returns whether the workspace ended up connected. On a live backend without
 * transport-level mocking this depends on the supplier accepting the synthetic
 * auth code — which it will not — so callers must treat `false` as expected
 * outside CI and skip rather than fail.
 */
export async function connectAliExpressViaApi(
  request: APIRequestContext,
  accessToken: string,
): Promise<boolean> {
  const headers = { Authorization: `Bearer ${accessToken}` };

  const connect = await request.post(`${API_URL}/api/v1/integrations/aliexpress/connect`, {
    headers,
    data: {},
  });
  if (connect.status() !== 201) {
    return false;
  }

  const { state } = (await connect.json()) as { state: string };
  const callback = await request.get(
    `${API_URL}/api/v1/integrations/aliexpress/callback?code=auth-code&state=${encodeURIComponent(state)}`,
    { maxRedirects: 0 },
  );

  if (callback.status() !== 303) {
    return false;
  }

  const location = callback.headers()["location"] ?? "";
  return location.includes("aliexpress=connected");
}

/** Import a supplier product and return the created catalogue row. */
export async function importProductViaApi(
  request: APIRequestContext,
  accessToken: string,
  externalId: string = FIXTURE_PRODUCT_ID,
): Promise<{ id: string; title: string; externalId: string } | null> {
  const response = await request.post(`${API_URL}/api/v1/products/import`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    data: { externalId },
  });

  if (!response.ok()) {
    return null;
  }

  return (await response.json()) as { id: string; title: string; externalId: string };
}

/**
 * Seed a connected tenant with one imported product entirely through the API.
 *
 * Skips cleanly when OAuth or import cannot complete — the common case against
 * a developer backend talking to the real AliExpress gateway without mocks.
 */
export async function seedCatalogueViaApi(request: APIRequestContext): Promise<{
  account: TestAccount;
  product: { id: string; title: string; externalId: string };
} | null> {
  const registered = await registerViaApi(request);
  const connected = await connectAliExpressViaApi(request, registered.accessToken);
  if (!connected) {
    return null;
  }

  const product = await importProductViaApi(request, registered.accessToken);
  if (!product) {
    return null;
  }

  return { account: registered.account, product };
}

/**
 * Register a unique tenant and insert one draft via the DB seed helper.
 *
 * Used by editor suites that must not depend on AliExpress credentials or on
 * host-exported `E2E_PRODUCT_ID` / `E2E_EMAIL` / `E2E_PASSWORD` that may point
 * at a different isolated database.
 */
export async function seedEditorFixtureViaDb(
  request: APIRequestContext,
): Promise<{
  account: TestAccount;
  productId: string;
  accessToken: string;
} | null> {
  const { canSeed, seedDrafts } = await import("./seed");
  if (!(await canSeed())) {
    return null;
  }

  const registered = await registerViaApi(request);
  const prefix = `E2E editor ${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  await seedDrafts(registered.tenantId, 1, { prefix });

  const listed = await request.get(`${API_URL}/api/v1/drafts?size=1`, {
    headers: { Authorization: `Bearer ${registered.accessToken}` },
  });
  if (!listed.ok()) {
    return null;
  }
  const body = (await listed.json()) as { items?: Array<{ id: string }> };
  const productId = body.items?.[0]?.id;
  if (!productId) {
    return null;
  }

  return {
    account: registered.account,
    productId,
    accessToken: registered.accessToken,
  };
}

/**
 * Sign in through the UI using credentials from an API-seeded (or env) account.
 *
 * Prefer `nextPath` so the SPA client-navigates after login and keeps the
 * in-memory access token. A bare `/login` → `/dashboard` → hard `goto` to a
 * draft remounts the app and currently loses the session when the refresh
 * cookie cannot restore it.
 */
export async function signInWithAccount(
  page: import("@playwright/test").Page,
  account: TestAccount,
  nextPath: string = "/dashboard",
): Promise<void> {
  const { blockGoogleIdentityScript } = await import("./auth");
  await blockGoogleIdentityScript(page);

  const loginUrl =
    nextPath === "/dashboard"
      ? "/login"
      : `/login?next=${encodeURIComponent(nextPath)}`;
  await page.goto(loginUrl);
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  // exact: the Google GSI control's accessible name also contains "Sign in".
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  // Parse nextPath so `/drafts/:id?tab=description` compares pathname + query
  // correctly — a raw pathname===nextPath check never matches when a query is present.
  const expected = new URL(nextPath, "http://e2e.invalid");
  await page.waitForURL(
    (url) => {
      const pathOk =
        url.pathname === expected.pathname ||
        url.pathname.startsWith(`${expected.pathname}/`);
      if (!pathOk) return false;
      for (const [key, value] of expected.searchParams) {
        if (url.searchParams.get(key) !== value) return false;
      }
      return true;
    },
    { timeout: 30_000 },
  );
}

export { TEST_PASSWORD };
