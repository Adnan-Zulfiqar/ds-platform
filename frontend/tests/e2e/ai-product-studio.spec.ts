import { expect, test, type Page } from "@playwright/test";

import type { ProductVersion, StoreListing } from "@/types/api";

import {
  APPROVE_PATH,
  approvedPreview,
  buildPreview,
  CANDIDATE_ID,
  CANDIDATE_PATH,
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  error,
  mockStudio,
  PREVIEW_PATH,
  PUBLISH_PATH,
  REVIEW_URL,
  SECOND_STORE_ID,
  T0,
  T1,
  T2,
  type Handler,
} from "./helpers/ai-studio-fixture";
import { buildSyntheticProduct, demoPublishResult, syncedDemoListing } from "./helpers/editor-fixture";

/**
 * Phase 9 Stage 10 — AI Studio single-product review
 * (PHASE_9_STAGE_10_PLAN.md §35, content states §5a / review finding G-1).
 *
 * Route-mocked: no backend, no provider, no Shopify. Assertions are on the
 * wire — exact paths and bodies, and requests that must *not* happen — plus
 * the copy that tells the merchant what changed and what did not.
 */

test.use({ viewport: { width: 1440, height: 900 } });

const OTHER_AI_VERSION_ID = "c0c0c0c0-0000-4000-8000-000000000002";

function candidateGet(reply: (storeId: string | null, attempt: number) => ReturnType<Handler>): Handler {
  return (r, attempt) =>
    r.method === "GET" && r.path.endsWith(CANDIDATE_PATH) ? reply(r.search.get("storeId"), attempt) : undefined;
}

function on(method: string, suffix: string, reply: (attempt: number) => ReturnType<Handler>): Handler {
  return (r, attempt) => (r.method === method && r.path.endsWith(suffix) ? reply(attempt) : undefined);
}

async function generate(page: Page) {
  await page.getByTestId("ai-studio-generate").click();
  await expect(page.getByTestId("ai-studio-candidate")).toBeVisible();
}

async function approveFromDialog(page: Page) {
  await page.getByTestId("ai-studio-approve").click();
  await expect(page.getByTestId("ai-studio-approve-dialog")).toBeVisible();
  await page.getByTestId("ai-studio-approve-confirm").click();
}

test.describe("Preview", () => {
  test("generate sends only tone and store, renders the flat candidate as text, and keeps Publish off", async ({
    page,
  }) => {
    const mock = await mockStudio(page, {
      handlers: [
        on("POST", PREVIEW_PATH, () => ({
          status: 201,
          body: buildPreview({
            proposal: {
              ...buildPreview().proposal,
              description: '<img src=x onerror="window.__pwned=1">Plain words',
            },
          }),
        })),
      ],
    });
    await page.goto(REVIEW_URL);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Wireless Desk Lamp with USB Charging");
    await expect(page.getByTestId("ai-studio-no-candidate")).toBeVisible();

    await generate(page);

    const posts = mock.calls("POST", PREVIEW_PATH);
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({ tone: "professional", storeId: DEMO_STORE_ID });

    await expect(page).toHaveURL(new RegExp(`\\?candidate=${CANDIDATE_ID}$`));
    await expect(page.getByTestId("ai-studio-current-title")).toHaveText("Wireless Desk Lamp with USB Charging");
    await expect(page.getByTestId("ai-studio-current-description")).toHaveText(
      "A calm wireless desk lamp for home offices.",
    );
    await expect(page.getByTestId("ai-studio-candidate-title")).toHaveText(
      "Cordless LED Desk Lamp with USB-C Charging",
    );
    // Model output is text: the markup is visible characters, not an element.
    await expect(page.getByTestId("ai-studio-candidate-description")).toContainText('<img src=x onerror=');
    expect(await page.getByTestId("ai-studio-candidate-description").locator("img").count()).toBe(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();

    await expect(page.getByTestId("ai-studio-seo-proposal")).toContainText("Proposal only — not sent to Shopify");
    await expect(page.getByTestId("ai-studio-not-approved-badge")).toBeVisible();
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();
    await expect(
      page.getByTestId("ai-studio-pipeline-blockers").locator('[data-code="candidate_not_approved"]'),
    ).toBeVisible();
    expect(mock.unhandled).toEqual([]);
  });

  test("reloading the pinned candidate GETs it and never generates again", async ({ page }) => {
    const mock = await mockStudio(page, {
      handlers: [
        on("POST", PREVIEW_PATH, () => ({ status: 201, body: buildPreview() })),
        candidateGet(() => ({ status: 200, body: buildPreview() })),
      ],
    });
    await page.goto(REVIEW_URL);
    await generate(page);
    await page.reload();
    await expect(page.getByTestId("ai-studio-candidate-title")).toHaveText(
      "Cordless LED Desk Lamp with USB-C Charging",
    );
    expect(mock.calls("POST", PREVIEW_PATH)).toHaveLength(1);
    expect(mock.calls("GET", CANDIDATE_PATH).length).toBeGreaterThanOrEqual(1);
  });

  test("an AI outage is explained with its reference and never retried on its own", async ({ page }) => {
    const mock = await mockStudio(page, {
      handlers: [on("POST", PREVIEW_PATH, () => error(503, "ai_error"))],
    });
    await page.goto(REVIEW_URL);
    await page.getByTestId("ai-studio-generate").click();

    const alert = page.getByTestId("ai-studio-preview-error");
    await expect(alert).toContainText("AI preview is unavailable right now.");
    await expect(alert).toContainText("req-ai_error");
    await expect(alert).not.toContainText("not configured");
    await page.waitForTimeout(1_500);
    expect(mock.calls("POST", PREVIEW_PATH)).toHaveLength(1);

    await page.getByTestId("ai-studio-generate").click();
    await expect.poll(() => mock.calls("POST", PREVIEW_PATH).length).toBe(2);
  });

  test("a test-provider candidate says so and cannot be published", async ({ page }) => {
    await mockStudio(page, {
      handlers: [
        candidateGet(() => ({
          status: 200,
          body: approvedPreview({ isSynthetic: true, provider: "stub", publishable: false }),
        })),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-test-preview")).toContainText("Test AI preview");
    await expect(page.getByTestId("ai-studio-test-preview")).toContainText("cannot be published to Shopify");
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();
  });

  test("an unknown product is not found", async ({ page }) => {
    await mockStudio(page, {
      handlers: [
        (r) =>
          r.method === "GET" && r.path.endsWith(`/products/${DEMO_PRODUCT_ID}`)
            ? error(404, "not_found", [{ type: "resource", message: "Product" }])
            : undefined,
      ],
    });
    await page.goto(REVIEW_URL);
    await expect(page.getByTestId("ai-studio-product-not-found")).toContainText("Product not found");
  });
});

test.describe("Approve and publish tokens", () => {
  test("approve sends only the approval token, changes nothing visible, then publish uses the new token", async ({
    page,
  }) => {
    let approved = false;
    const listing = syncedDemoListing({ contentSource: "product", contentVersionId: null });
    const mock = await mockStudio(page, {
      listings: [listing],
      handlers: [
        on("POST", PREVIEW_PATH, () => ({ status: 201, body: buildPreview() })),
        on("POST", APPROVE_PATH, () => {
          approved = true;
          return { status: 200, body: buildSyntheticProduct({ updatedAt: T1 }) };
        }),
        candidateGet(() => ({ status: 200, body: approved ? approvedPreview() : buildPreview() })),
        on("POST", PUBLISH_PATH, () => ({
          status: 200,
          body: demoPublishResult({ contentSource: "ai_version", contentVersionId: CANDIDATE_ID }),
        })),
      ],
    });
    await page.goto(REVIEW_URL);
    await generate(page);
    await expect(page.getByTestId("ai-studio-live-on-shopify")).toHaveText("Live on Shopify: Your draft text");

    await page.getByTestId("ai-studio-approve").click();
    const body = page.getByTestId("ai-studio-approve-dialog-body");
    await expect(body).toHaveText(
      "Makes this the approved AI version. Your current draft text remains unchanged. Nothing changes on Shopify until you publish.",
    );
    await expect(body).not.toContainText("replaces");
    await page.getByTestId("ai-studio-approve-confirm").click();

    await expect(page.getByTestId("ai-studio-approved")).toHaveText(
      "Approved. Your draft and your Shopify listing are unchanged.",
    );
    const approves = mock.calls("POST", APPROVE_PATH);
    expect(approves).toHaveLength(1);
    expect(approves[0]?.body).toEqual({ expectedUpdatedAt: T0 });
    // Nothing reached Shopify, and the draft column and live line did not move.
    expect(mock.calls("POST", PUBLISH_PATH)).toHaveLength(0);
    expect(mock.calls("POST", "/integrations/shopify/publish")).toHaveLength(0);
    await expect(page.getByTestId("ai-studio-current-title")).toHaveText("Wireless Desk Lamp with USB Charging");
    await expect(page.getByTestId("ai-studio-live-on-shopify")).toHaveText("Live on Shopify: Your draft text");

    // The follow-up GET for the selected store is what enables Publish.
    await expect(page.getByTestId("ai-studio-publish")).toBeEnabled();
    const gets = mock.calls("GET", CANDIDATE_PATH);
    expect(gets.at(-1)?.search.get("storeId")).toBe(DEMO_STORE_ID);
    await expect(page.getByTestId("ai-studio-approve")).toHaveCount(0);

    await page.getByTestId("ai-studio-publish").click();
    await expect(page.getByTestId("ai-studio-publish-ok")).toBeVisible();
    const publishes = mock.calls("POST", PUBLISH_PATH);
    expect(publishes).toHaveLength(1);
    expect(publishes[0]?.body).toEqual({ storeId: DEMO_STORE_ID, expectedUpdatedAt: T1 });
  });

  test("a later server token from the follow-up GET replaces the approve response's", async ({ page }) => {
    let approved = false;
    const mock = await mockStudio(page, {
      handlers: [
        on("POST", PREVIEW_PATH, () => ({ status: 201, body: buildPreview() })),
        on("POST", APPROVE_PATH, () => {
          approved = true;
          return { status: 200, body: buildSyntheticProduct({ updatedAt: T1 }) };
        }),
        candidateGet(() => ({
          status: 200,
          body: approved ? approvedPreview({ approvalExpectedUpdatedAt: T2 }) : buildPreview(),
        })),
        on("POST", PUBLISH_PATH, () => ({ status: 200, body: demoPublishResult() })),
      ],
    });
    await page.goto(REVIEW_URL);
    await generate(page);
    await approveFromDialog(page);
    await expect(page.getByTestId("ai-studio-publish")).toBeEnabled();
    await page.getByTestId("ai-studio-publish").click();
    await expect.poll(() => mock.calls("POST", PUBLISH_PATH).length).toBe(1);
    expect(mock.calls("POST", PUBLISH_PATH)[0]?.body).toEqual({ storeId: DEMO_STORE_ID, expectedUpdatedAt: T2 });
  });

  test("if the follow-up GET fails, Publish stays off until it succeeds", async ({ page }) => {
    let approved = false;
    let failGet = true;
    await mockStudio(page, {
      handlers: [
        on("POST", PREVIEW_PATH, () => ({ status: 201, body: buildPreview() })),
        on("POST", APPROVE_PATH, () => {
          approved = true;
          return { status: 200, body: buildSyntheticProduct({ updatedAt: T1 }) };
        }),
        candidateGet(() => {
          if (!approved) return { status: 200, body: buildPreview() };
          if (failGet) return error(500, "internal_error");
          return { status: 200, body: approvedPreview() };
        }),
      ],
    });
    await page.goto(REVIEW_URL);
    await generate(page);
    await approveFromDialog(page);
    await expect(page.getByTestId("ai-studio-candidate-error")).toBeVisible();
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();

    failGet = false;
    await page.getByTestId("ai-studio-candidate-error").getByRole("button", { name: "Try again" }).click();
    await expect(page.getByTestId("ai-studio-publish")).toBeEnabled();
  });

  test("reopening an already-approved candidate needs no approval and publishes with its token", async ({
    page,
  }) => {
    const mock = await mockStudio(page, {
      handlers: [
        candidateGet(() => ({ status: 200, body: approvedPreview() })),
        on("POST", PUBLISH_PATH, () => ({ status: 200, body: demoPublishResult() })),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-approved-badge")).toBeVisible();
    await expect(page.getByTestId("ai-studio-approve")).toHaveCount(0);
    await page.getByTestId("ai-studio-publish").click();
    await expect.poll(() => mock.calls("POST", PUBLISH_PATH).length).toBe(1);
    expect(mock.calls("POST", APPROVE_PATH)).toHaveLength(0);
    expect(mock.calls("POST", PUBLISH_PATH)[0]?.body).toEqual({ storeId: DEMO_STORE_ID, expectedUpdatedAt: T1 });
  });

  test("changing the store GETs the same candidate for it and never generates", async ({ page }) => {
    const mock = await mockStudio(page, {
      stores: "two",
      handlers: [
        candidateGet((storeId) =>
          storeId === SECOND_STORE_ID
            ? { status: 200, body: approvedPreview({ publishable: false }), delayMs: 300 }
            : { status: 200, body: approvedPreview() },
        ),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await page.locator("#ai-studio-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("ai-studio-publish")).toBeEnabled();

    await page.locator("#ai-studio-store").selectOption(SECOND_STORE_ID);
    // The first store's publishable answer never enables the button for the second.
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();
    await expect.poll(() => mock.calls("GET", CANDIDATE_PATH).at(-1)?.search.get("storeId")).toBe(SECOND_STORE_ID);
    await page.waitForTimeout(600);
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();
    expect(mock.calls("POST", PREVIEW_PATH)).toHaveLength(0);
  });
});

test.describe("Stale and lost responses", () => {
  test("a stale blocker disables approval; only an explicit click generates a fresh preview", async ({ page }) => {
    const mock = await mockStudio(page, {
      handlers: [
        candidateGet(() => ({
          status: 200,
          body: buildPreview({
            pipelineBlockers: [
              { code: "candidate_not_approved", message: "Approve first." },
              { code: "stale_preview", message: "The product changed after this preview." },
            ],
          }),
        })),
        on("POST", PREVIEW_PATH, () => ({ status: 201, body: buildPreview() })),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-stale")).toBeVisible();
    await expect(page.getByTestId("ai-studio-candidate-title")).toBeVisible();
    await expect(page.getByTestId("ai-studio-approve")).toBeDisabled();
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();
    await page.waitForTimeout(500);
    expect(mock.calls("POST", APPROVE_PATH)).toHaveLength(0);
    expect(mock.calls("POST", PREVIEW_PATH)).toHaveLength(0);

    await page.getByTestId("ai-studio-regenerate").click();
    await expect.poll(() => mock.calls("POST", PREVIEW_PATH).length).toBe(1);
  });

  test("an approve that meets a concurrent change shows the stale state and is not retried", async ({ page }) => {
    const mock = await mockStudio(page, {
      handlers: [
        candidateGet(() => ({ status: 200, body: buildPreview() })),
        on("POST", APPROVE_PATH, () => error(409, "conflict", [{ type: "reason", message: "stale_preview" }])),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await approveFromDialog(page);
    await expect(page.getByTestId("ai-studio-stale")).toBeVisible();
    await expect(page.getByTestId("ai-studio-approve")).toBeDisabled();
    await page.waitForTimeout(500);
    expect(mock.calls("POST", APPROVE_PATH)).toHaveLength(1);
  });

  test("a lost approve response is retried with the same token and the returned token is adopted", async ({
    page,
  }) => {
    let approved = false;
    const mock = await mockStudio(page, {
      handlers: [
        on("POST", APPROVE_PATH, (attempt) => {
          approved = true;
          // The server applied the first approve, but its response was lost.
          if (attempt === 1) return { status: 0, abort: true };
          return { status: 200, body: buildSyntheticProduct({ updatedAt: T1 }) };
        }),
        candidateGet(() => ({ status: 200, body: approved ? approvedPreview() : buildPreview() })),
        on("POST", PUBLISH_PATH, () => ({ status: 200, body: demoPublishResult() })),
      ],
    });
    // Keep the candidate inactive on screen after the lost response: the
    // client never learned it was approved.
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await approveFromDialog(page);
    await expect(page.getByTestId("ai-studio-approve-error")).toContainText("approving again is safe");
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();

    await approveFromDialog(page);
    await expect(page.getByTestId("ai-studio-approved")).toBeVisible();
    const approves = mock.calls("POST", APPROVE_PATH);
    expect(approves).toHaveLength(2);
    expect(approves[1]?.body).toEqual({ expectedUpdatedAt: T0 });
    await expect(page.getByTestId("ai-studio-publish")).toBeEnabled();
    await page.getByTestId("ai-studio-publish").click();
    await expect.poll(() => mock.calls("POST", PUBLISH_PATH).length).toBe(1);
    expect(mock.calls("POST", PUBLISH_PATH)[0]?.body).toEqual({ storeId: DEMO_STORE_ID, expectedUpdatedAt: T1 });
  });
});

test.describe("Content states (G-1, E-1)", () => {
  const versions: ProductVersion[] = [
    {
      id: OTHER_AI_VERSION_ID,
      versionNumber: 2,
      source: "ai_generated",
      title: "Earlier approved title",
      description: null,
      active: false,
      isPipelineCandidate: true,
      aiProvider: "openai",
      promptExecutionId: null,
      createdByUserId: null,
      createdAt: T0,
    },
  ];

  for (const [name, listing, expected] of [
    ["the draft text", syncedDemoListing({ contentSource: "product" }), "Live on Shopify: Your draft text"],
    [
      "this candidate",
      syncedDemoListing({ contentSource: "ai_version", contentVersionId: CANDIDATE_ID }),
      "Live on Shopify: Approved AI version 3",
    ],
    [
      "another AI version",
      syncedDemoListing({ contentSource: "ai_version", contentVersionId: OTHER_AI_VERSION_ID }),
      "Live on Shopify: Approved AI version 2",
    ],
  ] as [string, StoreListing, string][]) {
    test(`the live line names ${name} from the listing, not from approval`, async ({ page }) => {
      await mockStudio(page, {
        listings: [listing],
        versions,
        handlers: [candidateGet(() => ({ status: 200, body: approvedPreview() }))],
      });
      await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
      await expect(page.getByTestId("ai-studio-live-on-shopify")).toHaveText(expected);
    });
  }

  test("after publishing, the live line follows the refetched listing", async ({ page }) => {
    let published = false;
    await mockStudio(page, {
      handlers: [
        candidateGet(() => ({ status: 200, body: approvedPreview() })),
        on("POST", PUBLISH_PATH, () => {
          published = true;
          return {
            status: 200,
            body: demoPublishResult({ contentSource: "ai_version", contentVersionId: CANDIDATE_ID }),
          };
        }),
        on("GET", `/drafts/${DEMO_PRODUCT_ID}/listings`, () => ({
          status: 200,
          body: published
            ? [syncedDemoListing({ contentSource: "ai_version", contentVersionId: CANDIDATE_ID })]
            : [syncedDemoListing({ contentSource: "product" })],
        })),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-live-on-shopify")).toHaveText("Live on Shopify: Your draft text");
    await page.getByTestId("ai-studio-publish").click();
    await expect(page.getByTestId("ai-studio-live-on-shopify")).toHaveText(
      "Live on Shopify: Approved AI version 3",
    );
  });
});

test.describe("Evidence panels", () => {
  test("scores are labelled against the original baseline, never as confidence", async ({ page }) => {
    await mockStudio(page, { handlers: [candidateGet(() => ({ status: 200, body: buildPreview() }))] });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    const panel = page.getByTestId("ai-studio-quality");
    await expect(panel.getByTestId("ai-studio-quality-score")).toHaveText("78");
    await expect(panel.getByTestId("ai-studio-quality-baseline")).toHaveText("61");
    await expect(panel.getByTestId("ai-studio-quality-delta")).toHaveText("+17");
    await expect(panel).toContainText("Original baseline score");
    await expect(panel).toContainText("Change vs original");
    await expect(panel.getByTestId("ai-studio-quality-delta-copy")).toHaveText("Higher than the original baseline");
    const text = (await panel.textContent()) ?? "";
    expect(text).not.toMatch(/confidence|current score|previous score|\[object Object\]/i);
    await expect(panel.getByTestId("ai-studio-quality-breakdown")).toContainText("Points earned: 78 of 100");
  });

  test("each image status renders without failing the page", async ({ page }) => {
    const analysis = {
      imageAnalysisVersion: 1,
      sourceUrl: "https://ae01.alicdn.com/a.jpg",
      contentSha256: "abc",
      byteLength: 10,
      decodedWidth: 10,
      decodedHeight: 10,
      decodedFormat: "JPEG",
      status: "succeeded",
      errorCode: null,
      checks: {
        blur: { applicable: true, blurScore: 300, isBlurry: false, threshold: 100, workingSize: 512 },
        duplicates: { applicable: true, contentSha256: "abc", duplicateOfImageIds: [] },
        watermark: { applicable: false, reason: "not_implemented" },
      },
      captionProposal: "A lamp on a desk",
      altTextProposal: "Desk lamp",
      isSynthetic: true,
      provider: "stub",
      model: null,
      promptName: null,
      promptVersion: null,
    };
    const image = (position: number, status: string, extra: Record<string, unknown> = {}) => ({
      imageId: `0000000${position}-0000-4000-8000-000000000000`,
      position,
      status,
      errorCode: null,
      analysis: { ...analysis, status },
      ...extra,
    });
    await mockStudio(page, {
      handlers: [
        candidateGet(() => ({
          status: 200,
          body: buildPreview({
            imageAnalysis: {
              productId: DEMO_PRODUCT_ID,
              images: [
                image(0, "succeeded"),
                image(1, "checksOnly"),
                image(2, "fetchFailed", { errorCode: "ImageFetchDecodeFailed", analysis: { ...analysis, status: "fetchFailed", checks: null } }),
                image(3, "unknown", { analysis: null }),
                image(4, "somethingNew"),
              ],
            },
          }),
        })),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    const rows = page.getByTestId("ai-studio-image-row");
    await expect(rows).toHaveCount(5);
    await expect(rows.nth(0)).toHaveAttribute("data-state", "succeeded");
    await expect(rows.nth(0)).toContainText("Caption: A lamp on a desk");
    await expect(rows.nth(0)).toContainText("Test caption");
    await expect(rows.nth(1)).toHaveAttribute("data-state", "checks-only");
    await expect(rows.nth(1)).not.toContainText("Analysis unavailable");
    await expect(rows.nth(2)).toContainText("Analysis unavailable");
    await expect(rows.nth(2)).toContainText("ImageFetchDecodeFailed");
    await expect(rows.nth(3)).toContainText("Not analyzed");
    await expect(rows.nth(4)).toContainText("Analysis unavailable");
  });
});

test.describe("Versions the pipeline will not review", () => {
  test("a legacy version offers Activate; invalid metadata stops without a loop", async ({ page }) => {
    const mock = await mockStudio(page, {
      versions: [
        {
          id: CANDIDATE_ID,
          versionNumber: 3,
          source: "ai_generated",
          title: "Legacy optimize title",
          description: null,
          active: false,
          isPipelineCandidate: false,
          aiProvider: "stub",
          promptExecutionId: null,
          createdByUserId: null,
          createdAt: T0,
        },
      ],
      handlers: [
        candidateGet(() => error(422, "validation_error", [{ type: "reason", message: "not_a_pipeline_candidate" }])),
        on("POST", `/versions/${CANDIDATE_ID}/activate`, () =>
          error(422, "validation_error", [{ type: "reason", message: "not_a_pipeline_candidate" }]),
        ),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-legacy-version")).toContainText("Legacy optimize title");
    await expect(page.getByTestId("ai-studio-approve")).toHaveCount(0);
    await page.getByTestId("ai-studio-legacy-activate").click();
    await expect(page.getByTestId("ai-studio-legacy-invalid")).toContainText(
      "This version cannot be activated because its AI version metadata is invalid.",
    );
    await page.waitForTimeout(500);
    expect(mock.calls("POST", `/versions/${CANDIDATE_ID}/activate`)).toHaveLength(1);
    await expect(page.getByTestId("ai-studio-legacy-activate")).toHaveCount(0);
  });

  test("Activate refused for an unapproved pipeline candidate points back to approval", async ({ page }) => {
    await mockStudio(page, {
      versions: [
        {
          id: CANDIDATE_ID,
          versionNumber: 3,
          source: "ai_generated",
          title: "Some title",
          description: null,
          active: false,
          isPipelineCandidate: false,
          aiProvider: "stub",
          promptExecutionId: null,
          createdByUserId: null,
          createdAt: T0,
        },
      ],
      handlers: [
        candidateGet(() => error(422, "validation_error", [{ type: "reason", message: "not_a_pipeline_candidate" }])),
        on("POST", `/versions/${CANDIDATE_ID}/activate`, () =>
          error(422, "validation_error", [{ type: "reason", message: "pipeline_candidate_requires_approval" }]),
        ),
      ],
    });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await page.getByTestId("ai-studio-legacy-activate").click();
    await expect(page.getByTestId("ai-studio-legacy-error")).toContainText("Approve this version in AI Studio");
  });
});

test.describe("Roles", () => {
  test("owners and admins see AI Studio in navigation", async ({ page }) => {
    await mockStudio(page, { roles: ["admin"] });
    await page.goto("/ai-studio");
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: /^AI Studio/ })).toBeVisible();
  });

  test("members see no Studio link and the page explains instead of calling the API", async ({ page }) => {
    const mock = await mockStudio(page, { roles: ["member"] });
    await page.goto(`${REVIEW_URL}?candidate=${CANDIDATE_ID}`);
    await expect(page.getByTestId("ai-studio-permission")).toContainText(
      "AI Studio is available to owners and admins.",
    );
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Drafts" })).toBeVisible();
    await expect(nav.getByRole("link", { name: /^AI Studio/ })).toHaveCount(0);
    await page.waitForTimeout(500);
    expect(mock.requests.filter((r) => r.path.includes("/pipeline/"))).toEqual([]);
  });
});
