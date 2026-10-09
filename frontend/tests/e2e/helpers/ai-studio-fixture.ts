import type { Page, Route } from "@playwright/test";

import type {
  PipelineBulkRun,
  PipelineBulkRunItem,
  PipelinePreview,
  Product,
  ProductDetail,
  ProductVersion,
  RoleName,
  StoreListing,
} from "@/types/api";

import { blockGoogleIdentityScript } from "./auth";
import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  mockAuthResponse,
  mockPublishReadiness,
  mockShopifyStoresResponse,
} from "./editor-fixture";

/**
 * Route-mocked API for the AI Studio specs (Phase 9 Stage 10).
 *
 * One catch-all route over `/api/v1/**` with a small dispatcher. Every
 * request is recorded (method, path, query, body) so a test asserts the
 * exact wire contract — paths, bodies, the absence of a request — instead of
 * what the UI happened to render. Test handlers run first; an unhandled API
 * call is answered 500 and recorded in `unhandled`, so a test that forgot a
 * mock fails loudly rather than passing on a fallback.
 */

export const T0 = "2026-10-02T10:00:00.000000Z";
export const T1 = "2026-10-02T10:00:05.000000Z";
export const T2 = "2026-10-02T10:00:09.000000Z";
export const CANDIDATE_ID = "c0c0c0c0-0000-4000-8000-000000000003";
export const SECOND_STORE_ID = "66666666-6666-4666-8666-666666666666";

export interface RecordedRequest {
  method: string;
  path: string;
  search: URLSearchParams;
  body: unknown;
}

export interface Reply {
  status: number;
  body?: unknown;
  delayMs?: number;
  /** Abort the connection instead of answering (a lost response). */
  abort?: boolean;
}

export type Handler = (request: RecordedRequest, attempt: number) => Reply | undefined;

export interface StudioMock {
  requests: RecordedRequest[];
  unhandled: RecordedRequest[];
  /** Requests matching `method` and a path suffix. */
  calls: (method: string, pathSuffix: string) => RecordedRequest[];
}

function page_(items: unknown[], page = 1, size = 20, totalItems = items.length) {
  const totalPages = Math.max(1, Math.ceil(totalItems / size));
  return {
    items,
    meta: { page, size, totalItems, totalPages, hasNext: page < totalPages, hasPrevious: page > 1 },
  };
}

export { page_ as pageOf };

export function buildPreview(overrides: Partial<PipelinePreview> = {}): PipelinePreview {
  return {
    productId: DEMO_PRODUCT_ID,
    candidateVersionId: CANDIDATE_ID,
    candidateVersionNumber: 3,
    candidateActive: false,
    sourceUpdatedAt: T0,
    approvalExpectedUpdatedAt: T0,
    original: {
      title: "Wireless Desk Lamp with USB Charging",
      description: "<p>A calm wireless desk lamp for home offices.</p>",
      seoTitle: null,
      seoDescription: null,
      keywords: null,
      tags: [],
    },
    proposal: {
      title: "Cordless LED Desk Lamp with USB-C Charging",
      description: "A cordless lamp for a calm desk.",
      seoTitle: "Cordless LED Desk Lamp",
      seoDescription: "A calm cordless lamp.",
      keywords: "desk lamp, cordless",
      tags: [],
    },
    qualityScore: 78,
    qualityBaseline: { versionNumber: 1, score: 61 },
    qualityDelta: 17,
    qualityScoreVersion: 1,
    qualityBreakdown: {
      earned: 78,
      applicableMax: 100,
      dimensions: {
        title: { points: 25, max: 30, applicable: true, length: 41 },
        description: { points: 20, max: 30, applicable: true, length: 33 },
        repetition: {
          points: 20,
          max: 20,
          applicable: true,
          checks: {
            titleNotStuffed: true,
            descriptionNotPhraseStuffed: true,
            descriptionNotDominated: true,
            descriptionDistinctFromTitle: true,
          },
        },
        keywordCoverage: {
          applicable: true,
          points: 13,
          max: 20,
          matched: 2,
          total: 3,
          source: "tags",
          keywords: ["lamp", "desk", "usb"],
          truncated: false,
        },
      },
      seoFormat: {
        seoTitleWithinRequestedBound: true,
        seoDescriptionWithinRequestedBound: true,
        keywordsPresent: true,
      },
    },
    imageAnalysis: { productId: DEMO_PRODUCT_ID, images: [] },
    isSynthetic: false,
    provider: "openai",
    channelReadiness: mockPublishReadiness() as PipelinePreview["channelReadiness"],
    pipelineBlockers: [{ code: "candidate_not_approved", message: "Approve this version before publishing." }],
    pipelineWarnings: [],
    publishable: false,
    ...overrides,
  };
}

/** The same candidate after approval, as the follow-up GET returns it. */
export function approvedPreview(overrides: Partial<PipelinePreview> = {}): PipelinePreview {
  return buildPreview({
    candidateActive: true,
    approvalExpectedUpdatedAt: T1,
    pipelineBlockers: [],
    publishable: true,
    ...overrides,
  });
}

export function buildRun(overrides: Partial<PipelineBulkRun> = {}): PipelineBulkRun {
  return {
    id: "a1a1a1a1-0000-4000-8000-000000000001",
    status: "pending",
    idempotencyKey: "k",
    tone: "professional",
    storeId: null,
    heartbeatAt: null,
    recoveryCount: 0,
    startedAt: null,
    finishedAt: null,
    createdAt: T0,
    totalCount: 2,
    processedCount: 0,
    succeededCount: 0,
    failedCount: 0,
    skippedCount: 0,
    missingCount: 0,
    failureReason: null,
    cancelRequestedAt: null,
    ...overrides,
  };
}

export function buildItem(overrides: Partial<PipelineBulkRunItem> = {}): PipelineBulkRunItem {
  return {
    submittedProductId: DEMO_PRODUCT_ID,
    productId: DEMO_PRODUCT_ID,
    state: "pending",
    candidateVersionId: null,
    errorCode: null,
    errorMessage: null,
    attemptCount: 0,
    finishedAt: null,
    ...overrides,
  };
}

export function listProduct(id: string, title: string): Product {
  const detail = buildSyntheticProduct({ id, title });
  return detail as unknown as Product;
}

export function error(status: number, code: string, details: { type: string; message: string }[] = []) {
  return {
    status,
    body: {
      code,
      message: `server message for ${code}`,
      details: details.map((detail) => ({ field: null, ...detail })),
      requestId: `req-${code}`,
    },
  };
}

export async function mockStudio(
  page: Page,
  options: {
    roles?: RoleName[];
    product?: ProductDetail;
    stores?: "one" | "two" | "none";
    listings?: StoreListing[];
    versions?: ProductVersion[];
    drafts?: Product[];
    published?: Product[];
    handlers?: Handler[];
  } = {},
): Promise<StudioMock> {
  const auth = mockAuthResponse();
  auth.identity.roles = options.roles ?? ["owner"];
  const product = options.product ?? buildSyntheticProduct({ updatedAt: T0 });
  const requests: RecordedRequest[] = [];
  const unhandled: RecordedRequest[] = [];
  const attempts = new Map<Handler, number>();

  const storesBody = (() => {
    const base = mockShopifyStoresResponse();
    if (options.stores === "none") return page_([], 1, 50);
    if (options.stores === "two") {
      const second = { ...base.items[0], id: SECOND_STORE_ID, name: "Second Shopify" };
      return page_([base.items[0], second], 1, 50);
    }
    return base;
  })();

  const defaults: Handler[] = [
    (r) => (r.path.endsWith("/auth/refresh") ? { status: 200, body: auth } : undefined),
    (r) => (r.path.endsWith("/auth/me") ? { status: 200, body: auth.identity } : undefined),
    (r) => (r.path.endsWith("/auth/logout") ? { status: 204 } : undefined),
    (r) =>
      r.path.endsWith("/products/workspace-counts")
        ? { status: 200, body: { drafts: 1, products: 0 } }
        : undefined,
    (r) =>
      r.path.endsWith("/notifications/unread-count") ? { status: 200, body: { unread: 0 } } : undefined,
    // The shell's platform banner (D-019): nothing to announce.
    (r) =>
      r.path.endsWith("/system/status")
        ? { status: 200, body: { maintenance: false, maintenanceMessage: null, announcements: [] } }
        : undefined,
    (r) => (r.path.endsWith("/notifications") ? { status: 200, body: page_([], 1, 8) } : undefined),
    (r) => (r.path.endsWith("/stores") ? { status: 200, body: storesBody } : undefined),
    (r) =>
      r.method === "GET" && r.path.endsWith(`/products/${product.id}`)
        ? { status: 200, body: product }
        : undefined,
    (r) =>
      r.method === "GET" && r.path.endsWith(`/products/${product.id}/versions`)
        ? { status: 200, body: page_(options.versions ?? [], 1, 50) }
        : undefined,
    (r) =>
      r.method === "GET" && r.path.endsWith(`/drafts/${product.id}/listings`)
        ? { status: 200, body: options.listings ?? [] }
        : undefined,
    (r) =>
      r.method === "GET" && r.path.endsWith("/api/v1/drafts")
        ? { status: 200, body: page_(options.drafts ?? [], Number(r.search.get("page") ?? 1)) }
        : undefined,
    (r) =>
      r.method === "GET" && r.path.endsWith("/api/v1/products")
        ? { status: 200, body: page_(options.published ?? [], Number(r.search.get("page") ?? 1)) }
        : undefined,
  ];
  const handlers = [...(options.handlers ?? []), ...defaults];

  await blockGoogleIdentityScript(page);
  await page.route(
    (url) => url.pathname.includes("/api/v1/"),
    async (route: Route) => {
      const request = route.request();
      const url = new URL(request.url());
      let body: unknown = null;
      try {
        body = request.postDataJSON();
      } catch {
        body = request.postData();
      }
      const recorded: RecordedRequest = {
        method: request.method(),
        path: url.pathname,
        search: url.searchParams,
        body,
      };
      requests.push(recorded);

      for (const handler of handlers) {
        const attempt = (attempts.get(handler) ?? 0) + 1;
        const reply = handler(recorded, attempt);
        if (!reply) continue;
        attempts.set(handler, attempt);
        if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
        if (reply.abort) return route.abort("connectionreset");
        return route.fulfill({
          status: reply.status,
          contentType: "application/json",
          body: reply.body === undefined ? "" : JSON.stringify(reply.body),
        });
      }
      unhandled.push(recorded);
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ code: "internal_error", message: "unmocked", details: [], requestId: "unmocked" }),
      });
    },
  );

  return {
    requests,
    unhandled,
    calls: (method, suffix) => requests.filter((r) => r.method === method && r.path.endsWith(suffix)),
  };
}

export const REVIEW_URL = `/ai-studio/products/${DEMO_PRODUCT_ID}`;
export const PREVIEW_PATH = `/products/${DEMO_PRODUCT_ID}/pipeline/preview`;
export const CANDIDATE_PATH = `/products/${DEMO_PRODUCT_ID}/pipeline/versions/${CANDIDATE_ID}/preview`;
export const APPROVE_PATH = `/products/${DEMO_PRODUCT_ID}/pipeline/versions/${CANDIDATE_ID}/approve`;
export const PUBLISH_PATH = `/products/${DEMO_PRODUCT_ID}/pipeline/versions/${CANDIDATE_ID}/publish`;
export { DEMO_PRODUCT_ID, DEMO_STORE_ID };
