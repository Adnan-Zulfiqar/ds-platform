/**
 * UX-L1 reviewer screenshot capture — not part of CI.
 * Run with: E2E_BASE_URL=http://127.0.0.1:3001 npx playwright test ux-screenshots.spec.ts
 */
import { test } from "@playwright/test";
import path from "node:path";

import {
  blockGoogleIdentityScript,
  isApiReachable,
  TEST_PASSWORD,
} from "./helpers/auth";
import { signInWithAccount } from "./helpers/catalogue";

const OUT = path.resolve(__dirname, "../../.ux-review/screenshots");

const PUBLIC_ROUTES = ["/", "/login", "/register", "/forgot-password", "/privacy", "/terms"];
const AUTH_ROUTES = [
  "/dashboard",
  "/drafts",
  "/products",
  "/stores",
  "/orders",
  "/inventory",
  "/analytics",
  "/settings",
  "/settings/integrations",
];
const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
] as const;

const UX_DEMO = {
  email: "ux-demo@example.com",
  password: TEST_PASSWORD,
  companyName: "UX Demo Store",
};

test.describe("UX-L1 screenshot matrix", () => {
  for (const route of PUBLIC_ROUTES) {
    for (const viewport of VIEWPORTS) {
      test(`${route} @ ${viewport.name}`, async ({ page }) => {
        await blockGoogleIdentityScript(page);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto(route, { waitUntil: "networkidle" });
        const slug = route === "/" ? "home" : route.replace(/\//g, "_").replace(/^_/, "");
        await page.screenshot({
          path: path.join(OUT, "public", `${slug}-${viewport.name}.png`),
          fullPage: true,
        });
      });
    }
  }

  test.describe("authenticated", () => {
    test.beforeAll(async () => {
      test.skip(!(await isApiReachable()), "Backend API is not reachable.");
    });

    for (const route of AUTH_ROUTES) {
      test(`${route} @ desktop`, async ({ page }) => {
        await blockGoogleIdentityScript(page);
        await page.setViewportSize({ width: 1440, height: 900 });
        await signInWithAccount(page, UX_DEMO, route);
        await page.waitForLoadState("networkidle");
        const slug = route.replace(/\//g, "_").replace(/^_/, "");
        await page.screenshot({
          path: path.join(OUT, "app", `${slug}-desktop.png`),
          fullPage: true,
        });
      });

      test(`${route} @ mobile`, async ({ page }) => {
        await blockGoogleIdentityScript(page);
        await page.setViewportSize({ width: 390, height: 844 });
        await signInWithAccount(page, UX_DEMO, route);
        await page.waitForLoadState("networkidle");
        const slug = route.replace(/\//g, "_").replace(/^_/, "");
        await page.screenshot({
          path: path.join(OUT, "app", `${slug}-mobile.png`),
          fullPage: true,
        });
      });
    }
  });
});
