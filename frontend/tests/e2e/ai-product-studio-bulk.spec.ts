import { expect, test, type Page } from "@playwright/test";

import {
  buildItem,
  buildRun,
  error,
  listProduct,
  mockStudio,
  pageOf,
  type Handler,
} from "./helpers/ai-studio-fixture";

/**
 * Phase 9 Stage 10 — AI Studio bulk runs (PHASE_9_STAGE_10_PLAN.md §18–§23,
 * §35 bulk list). Route-mocked; assertions are on request bodies and query
 * strings, and on requests that must not happen.
 */

const RUN_ID = "a1a1a1a1-0000-4000-8000-000000000001";
const RUN_PATH = `/products/pipeline/runs/${RUN_ID}`;

function id(n: number): string {
  return `d0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
}

const DRAFTS = Array.from({ length: 30 }, (_, n) => listProduct(id(n), `Draft product ${n}`));
const PUBLISHED = Array.from({ length: 30 }, (_, n) => listProduct(id(100 + n), `Published product ${n}`));

/** Lists that honour `page`/`size` the way the backend does. */
function lists(): Handler[] {
  const slice = (rows: typeof DRAFTS, search: URLSearchParams) => {
    const page = Number(search.get("page") ?? 1);
    const size = Number(search.get("size") ?? 25);
    return { ...pageOf(rows.slice((page - 1) * size, page * size), page, size, rows.length) };
  };
  return [
    (r) => (r.method === "GET" && r.path.endsWith("/api/v1/drafts") ? { status: 200, body: slice(DRAFTS, r.search) } : undefined),
    (r) =>
      r.method === "GET" && r.path.endsWith("/api/v1/products") ? { status: 200, body: slice(PUBLISHED, r.search) } : undefined,
  ];
}

async function checkRow(page: Page, title: string) {
  await page.getByRole("checkbox", { name: title, exact: true }).check();
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe("Selection", () => {
  test("drafts and published share one capped selection across pages and views", async ({ page }) => {
    const mock = await mockStudio(page, { handlers: lists() });
    await page.goto("/ai-studio");

    // Drafts is the default view; the toggles are pressed buttons, not tabs.
    const drafts = page.getByTestId("ai-studio-view-drafts");
    const published = page.getByTestId("ai-studio-view-published");
    await expect(drafts).toHaveAttribute("aria-pressed", "true");
    await expect(published).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByRole("tab")).toHaveCount(0);

    await checkRow(page, "Draft product 0");
    await page.getByRole("button", { name: "Next" }).click();
    await checkRow(page, "Draft product 20");

    // Keyboard: Tab to the Published toggle and switch with Enter.
    await published.focus();
    await page.keyboard.press("Enter");
    await expect(published).toHaveAttribute("aria-pressed", "true");
    await checkRow(page, "Published product 0");
    await expect(page.getByTestId("ai-studio-selection-count")).toHaveText("3 / 50 selected");

    // Lists ask with `size`, never `pageSize`.
    const listCalls = mock.requests.filter((r) => /\/api\/v1\/(drafts|products)$/.test(r.path));
    expect(listCalls.length).toBeGreaterThan(0);
    for (const call of listCalls) {
      expect(call.search.get("size")).toBe("20");
      expect(call.search.has("pageSize")).toBe(false);
    }
  });

  test("the 51st product cannot be selected", async ({ page }) => {
    await mockStudio(page, { handlers: lists() });
    await page.goto("/ai-studio");
    await page.getByRole("button", { name: "Select page" }).click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByRole("button", { name: "Select page" }).click();
    await page.getByTestId("ai-studio-view-published").click();
    await page.getByRole("button", { name: "Select page" }).click();
    await expect(page.getByTestId("ai-studio-selection-count")).toHaveText("50 / 50 selected");
    await expect(page.getByTestId("ai-studio-cap-note")).toHaveText("You can optimize up to 50 products at a time.");
    // Every checkbox on this page is now selected; an unselected one is refused.
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByRole("checkbox", { name: "Published product 20", exact: true })).toBeDisabled();
    await expect(page.getByTestId("ai-studio-selection-count")).toHaveText("50 / 50 selected");
  });
});

test.describe("Starting and following a run", () => {
  test("start sends exactly the selection; a retry reuses the key; polling stops at a terminal status", async ({
    page,
  }) => {
    let polls = 0;
    const mock = await mockStudio(page, {
      handlers: [
        ...lists(),
        (r, attempt) =>
          r.method === "POST" && r.path.endsWith("/products/pipeline/runs")
            ? attempt === 1
              ? error(503, "internal_error")
              : { status: 202, body: buildRun() }
            : undefined,
        (r) => {
          if (r.method !== "GET" || !r.path.endsWith(RUN_PATH)) return undefined;
          polls += 1;
          if (polls === 1) return { status: 200, body: buildRun({ status: "running", processedCount: 1 }) };
          return {
            status: 200,
            body: buildRun({
              status: "partial",
              processedCount: 2,
              succeededCount: 1,
              failedCount: 1,
              finishedAt: "2026-10-02T10:05:00Z",
            }),
          };
        },
        (r) =>
          r.method === "GET" && r.path.endsWith(`${RUN_PATH}/items`)
            ? {
                status: 200,
                body: pageOf([
                  buildItem({ submittedProductId: id(0), productId: id(0), state: "succeeded", candidateVersionId: "cand-0" }),
                  buildItem({ submittedProductId: id(1), productId: id(1), state: "failed", errorCode: "unexpected_error", attemptCount: 3 }),
                  buildItem({ submittedProductId: id(2), productId: null, state: "missing", errorCode: "product_not_found" }),
                ]),
              }
            : undefined,
      ],
    });
    await page.goto("/ai-studio");
    await checkRow(page, "Draft product 1");
    await checkRow(page, "Draft product 0");
    await page.getByTestId("ai-studio-bulk-start").click();
    await page.getByTestId("ai-studio-bulk-confirm").click();
    await expect(page.getByTestId("ai-studio-bulk-error")).toBeVisible();
    await page.getByTestId("ai-studio-bulk-confirm").click();

    await expect(page).toHaveURL(new RegExp(`\\?run=${RUN_ID}$`));
    const starts = mock.calls("POST", "/products/pipeline/runs");
    expect(starts).toHaveLength(2);
    const [first, second] = starts.map((s) => s.body as Record<string, unknown>);
    expect(first?.productIds).toEqual([id(0), id(1)].sort());
    expect(second?.idempotencyKey).toBe(first?.idempotencyKey);
    expect(second).toEqual(first);

    // Pending → running → partial, from the server's status, never "100% success".
    const status = page.getByTestId("ai-studio-run-status");
    await expect(status).toHaveText("Partial", { timeout: 15_000 });
    await expect(page.getByTestId("ai-studio-run-summary")).not.toContainText("100% success");

    // Item field is `state`; only a succeeded item with both ids opens a review.
    const items = page.getByTestId("ai-studio-run-item");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0).getByTestId("ai-studio-run-item-review")).toHaveAttribute(
      "href",
      `/ai-studio/products/${id(0)}?candidate=cand-0`,
    );
    await expect(items.nth(1).getByTestId("ai-studio-run-item-review")).toHaveCount(0);
    await expect(items.nth(2).getByTestId("ai-studio-run-item-review")).toHaveCount(0);

    // No further run GET after the terminal answer.
    const settled = mock.calls("GET", RUN_PATH).length;
    await page.waitForTimeout(6_000);
    expect(mock.calls("GET", RUN_PATH).length).toBe(settled);
  });

  test("cancel is cooperative and the run stays authoritative", async ({ page }) => {
    let cancelled = false;
    const mock = await mockStudio(page, {
      handlers: [
        (r) =>
          r.method === "POST" && r.path.endsWith(`${RUN_PATH}/cancel`)
            ? ((cancelled = true),
              { status: 200, body: buildRun({ status: "running", cancelRequestedAt: "2026-10-02T10:01:00Z" }) })
            : undefined,
        (r) =>
          r.method === "GET" && r.path.endsWith(RUN_PATH)
            ? {
                status: 200,
                body: cancelled
                  ? buildRun({ status: "running", cancelRequestedAt: "2026-10-02T10:01:00Z" })
                  : buildRun({ status: "running" }),
              }
            : undefined,
        (r) => (r.method === "GET" && r.path.endsWith(`${RUN_PATH}/items`) ? { status: 200, body: pageOf([]) } : undefined),
      ],
    });
    await page.goto(`/ai-studio?run=${RUN_ID}`);
    await page.getByTestId("ai-studio-run-cancel").click();
    await expect(page.getByTestId("ai-studio-run-cancel-dialog")).toContainText("Cancellation is not instant.");
    await page.getByTestId("ai-studio-run-cancel-confirm").click();
    await expect(page.getByTestId("ai-studio-run-status")).toHaveText("Cancelling…");
    await expect(page.getByTestId("ai-studio-run-cancel")).toHaveCount(0);
    expect(mock.calls("POST", `${RUN_PATH}/cancel`)).toHaveLength(1);
  });

  test("another active run is reported once and never retried", async ({ page }) => {
    const mock = await mockStudio(page, {
      handlers: [
        ...lists(),
        (r) =>
          r.method === "POST" && r.path.endsWith("/products/pipeline/runs")
            ? error(409, "pipeline_bulk_run_active")
            : undefined,
      ],
    });
    await page.goto("/ai-studio");
    await checkRow(page, "Draft product 0");
    await page.getByTestId("ai-studio-bulk-start").click();
    await page.getByTestId("ai-studio-bulk-confirm").click();
    await expect(page.getByTestId("ai-studio-bulk-error")).toContainText(
      "Another bulk optimization is already running for this workspace.",
    );
    await expect(page.getByTestId("ai-studio-bulk-confirm")).toBeDisabled();
    await page.waitForTimeout(500);
    expect(mock.calls("POST", "/products/pipeline/runs")).toHaveLength(1);
  });
});

test.describe("Mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("selection and its start action stay reachable without horizontal scroll", async ({ page }) => {
    await mockStudio(page, { handlers: lists() });
    await page.goto("/ai-studio");
    await checkRow(page, "Draft product 0");
    await expect(page.getByTestId("ai-studio-bulk-start")).toBeInViewport();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
