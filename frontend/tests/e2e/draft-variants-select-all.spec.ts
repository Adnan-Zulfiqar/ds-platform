import { expect, test } from "./fixtures/provider-isolation";
import { DEMO_PRODUCT_ID, buildSyntheticProduct, openMockedEditor } from "./helpers/editor-fixture";

/**
 * Variants: switch every variant on or off at once. One request for the
 * whole draft, not one per variant, and every row follows the server.
 */

function variant(index: number, isEnabled: boolean) {
  return {
    id: `33333333-3333-4333-8333-33333333333${index}`,
    externalVariantId: `v${index}`,
    externalAttributes: null,
    label: `Size: ${index}`,
    costPrice: "8.50",
    listPrice: "19.99",
    currency: "GBP",
    stockQuantity: 10,
    imageUrl: null,
    merchantSku: null,
    sellPrice: "19.99",
    compareAtPrice: null,
    isEnabled,
  };
}

test("enable all and disable all switch every variant in one request", async ({ page }) => {
  const initial = buildSyntheticProduct({
    variantCount: 3,
    variants: [variant(1, true), variant(2, false), variant(3, false)],
  });
  let served = initial;
  const bodies: unknown[] = [];
  await openMockedEditor(page, { product: initial });
  await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}/variants`, async (route) => {
    const body = route.request().postDataJSON() as { enabled: boolean };
    bodies.push(body);
    served = {
      ...served,
      variants: served.variants.map((v) => ({ ...v, isEnabled: body.enabled })),
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(served) });
  });
  await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}`, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(served) })
      : route.fallback(),
  );

  await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("editor-tab-variants").click();
  const panel = page.getByTestId("draft-variants-panel");
  await expect(panel).toContainText("1/3 enabled");

  await panel.getByRole("button", { name: "Enable all" }).click();
  await expect(panel).toContainText("3/3 enabled");
  await expect(page.getByTestId("draft-variants-select-all")).toBeChecked();
  for (const row of await panel.getByTestId("draft-variant-row").all()) {
    await expect(row.getByRole("checkbox")).toBeChecked();
  }

  await page.getByTestId("draft-variants-select-all").uncheck();
  await expect(panel).toContainText("0/3 enabled");
  expect(bodies).toEqual([{ enabled: true }, { enabled: false }]);
});
