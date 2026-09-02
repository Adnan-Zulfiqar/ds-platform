/**
 * UX-L1 — public homepage company disclosure (TikTok / ECR requirement).
 */
import { test, expect } from "@playwright/test";

const DISCLOSURE =
  "DropPilot AI is operated by DESIRLY LIMITED, a private limited company registered in England and Wales under company number 16381500.";
const OFFICE = "Registered office: 200 Eton Road Eton Road, Ilford, England, IG1 2UN.";

test.describe("Public homepage", () => {
  test("renders the statutory company disclosure in HTML", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    const html = await page.content();
    expect(html).toContain("DESIRLY LIMITED");
    expect(html).toContain("16381500");
    expect(html).toContain("200 Eton Road Eton Road, Ilford, England, IG1 2UN");
    await expect(page.getByTestId("company-disclosure")).toBeVisible();
    await expect(page.getByTestId("company-disclosure")).toContainText(DISCLOSURE);
    await expect(page.getByTestId("company-disclosure")).toContainText(OFFICE);
  });

  test("links to privacy and draft terms without requiring sign-in", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: /Privacy Policy/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Terms of Service/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /draft/i })).toBeVisible();
  });

  test("disclosure is present with JavaScript disabled", async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    const text = await page.locator("body").innerText();
    expect(text).toContain("DESIRLY LIMITED");
    expect(text).toContain("16381500");
    await context.close();
  });

  test("is usable on a mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByTestId("company-disclosure")).toBeVisible();
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
  });
});
