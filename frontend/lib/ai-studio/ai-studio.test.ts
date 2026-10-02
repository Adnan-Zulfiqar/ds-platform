import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api-client";
import {
  isCancelling,
  isTerminalRun,
  MAX_BULK_SELECTION,
  progressPercent,
  RUN_STATUS_LABEL,
  sameSnapshot,
  snapshotOf,
} from "@/lib/ai-studio/bulk";
import {
  blockerCodesOf,
  isStaleCandidateError,
  isStoreNotFound,
  reasonOf,
  studioErrorMessage,
} from "@/lib/ai-studio/errors";
import { stripHtml } from "@/lib/ai-studio/text";
import {
  approvalTokenOf,
  hasStalePreviewBlocker,
  isTestPreview,
  newerToken,
  publishTokenOf,
} from "@/lib/ai-studio/tokens";
import type { PipelinePreview } from "@/types/api";

const T0 = "2026-10-02T10:00:00.000000Z";
const T1 = "2026-10-02T10:00:05.000000Z";

function preview(overrides: Partial<PipelinePreview> = {}): PipelinePreview {
  return {
    productId: "p",
    candidateVersionId: "v",
    candidateVersionNumber: 3,
    candidateActive: false,
    sourceUpdatedAt: T0,
    approvalExpectedUpdatedAt: T0,
    original: { title: "a", description: null, seoTitle: null, seoDescription: null, keywords: null, tags: [] },
    proposal: { title: "b", description: null, seoTitle: null, seoDescription: null, keywords: null, tags: [] },
    qualityScore: null,
    qualityBaseline: null,
    qualityDelta: null,
    qualityScoreVersion: null,
    qualityBreakdown: null,
    imageAnalysis: { productId: "p", images: [] },
    isSynthetic: false,
    provider: "openai",
    channelReadiness: null,
    pipelineBlockers: [{ code: "candidate_not_approved", message: "Approve first." }],
    pipelineWarnings: [],
    publishable: false,
    ...overrides,
  };
}

function apiError(code: string, details: { type: string; message: string }[] = []): ApiError {
  return new ApiError({
    code,
    message: "envelope message",
    status: 409,
    details: details.map((detail) => ({ field: null, ...detail })),
  });
}

describe("token meaning follows candidateActive", () => {
  it("an inactive candidate yields only an approval token", () => {
    expect(approvalTokenOf(preview())).toBe(T0);
    expect(publishTokenOf(preview())).toBeNull();
  });

  it("an active candidate yields only a publish token", () => {
    const active = preview({ candidateActive: true, approvalExpectedUpdatedAt: T1 });
    expect(publishTokenOf(active)).toBe(T1);
    expect(approvalTokenOf(active)).toBeNull();
  });

  it("a later server token replaces the approve response's token, never the reverse", () => {
    expect(newerToken(T0, T1)).toBe(T1);
    expect(newerToken(T1, T0)).toBe(T1);
    expect(newerToken(null, T0)).toBe(T0);
    expect(newerToken(T0, null)).toBe(T0);
  });
});

describe("stale and synthetic classification reads only server fields", () => {
  it("a stale blocker on an inactive candidate disables approval", () => {
    const stale = preview({
      pipelineBlockers: [
        { code: "candidate_not_approved", message: "" },
        { code: "stale_preview", message: "" },
      ],
    });
    expect(hasStalePreviewBlocker(stale)).toBe(true);
    expect(hasStalePreviewBlocker(preview())).toBe(false);
  });

  it("the stub provider is a test preview even if isSynthetic were false", () => {
    expect(isTestPreview(preview({ provider: "stub" }))).toBe(true);
    expect(isTestPreview(preview({ isSynthetic: true }))).toBe(true);
    expect(isTestPreview(preview())).toBe(false);
  });
});

describe("error reasons come from details, never from the message", () => {
  it("reads the reason entry", () => {
    const error = apiError("conflict", [{ type: "reason", message: "stale_preview" }]);
    expect(reasonOf(error)).toBe("stale_preview");
    expect(isStaleCandidateError(error)).toBe(true);
    expect(studioErrorMessage(error)).toBe("This product changed after the preview was generated.");
  });

  it("a conflict without a stale reason is not a stale candidate", () => {
    expect(isStaleCandidateError(apiError("conflict"))).toBe(false);
  });

  it("recognises a missing store only by the resource detail", () => {
    const missing = apiError("not_found", [{ type: "resource", message: "Store" }]);
    expect(isStoreNotFound(missing)).toBe(true);
    expect(isStoreNotFound(apiError("not_found", [{ type: "resource", message: "Product" }]))).toBe(false);
  });

  it("splits publish_blocked blocker codes", () => {
    const blocked = apiError("validation_error", [
      { type: "reason", message: "publish_blocked" },
      { type: "blocker_codes", message: "missing_price, missing_image" },
    ]);
    expect(blockerCodesOf(blocked)).toEqual(["missing_price", "missing_image"]);
  });

  it("never echoes the envelope message for a mapped code", () => {
    expect(studioErrorMessage(apiError("ai_error"))).toBe("AI preview is unavailable right now.");
    expect(studioErrorMessage(new Error("boom"))).toBe("Something went wrong. Please try again.");
  });
});

describe("bulk run helpers", () => {
  it("labels come from status; a fully processed partial run stays Partial", () => {
    expect(RUN_STATUS_LABEL.partial).toBe("Partial");
    expect(progressPercent({ processedCount: 50, totalCount: 50 })).toBe(100);
    expect(progressPercent({ processedCount: 0, totalCount: 0 })).toBe(0);
  });

  it("terminal statuses stop polling; pending and running do not", () => {
    expect(isTerminalRun("completed")).toBe(true);
    expect(isTerminalRun("cancelled")).toBe(true);
    expect(isTerminalRun("running")).toBe(false);
    expect(isTerminalRun("pending")).toBe(false);
  });

  it("a running run with a cancel request is cancelling", () => {
    expect(isCancelling({ status: "running", cancelRequestedAt: T0 })).toBe(true);
    expect(isCancelling({ status: "running", cancelRequestedAt: null })).toBe(false);
    expect(isCancelling({ status: "cancelled", cancelRequestedAt: T0 })).toBe(false);
  });

  it("the same selection in another order is the same intent", () => {
    const a = snapshotOf(["b", "a", "a"], "professional", null);
    const b = snapshotOf(["a", "b"], "professional", null);
    expect(a.productIds).toEqual(["a", "b"]);
    expect(sameSnapshot(a, b)).toBe(true);
    expect(sameSnapshot(a, snapshotOf(["a", "b"], "luxury", null))).toBe(false);
    expect(sameSnapshot(a, snapshotOf(["a", "b"], "professional", "store"))).toBe(false);
    expect(sameSnapshot(null, b)).toBe(false);
  });

  it("the cap is the backend's 50", () => {
    expect(MAX_BULK_SELECTION).toBe(50);
  });
});

describe("stripHtml renders markup as text", () => {
  it("drops tags and script bodies", () => {
    expect(stripHtml('<p>Hi <img src=x onerror="alert(1)"></p><script>alert(2)</script>')).toBe("Hi");
  });
});
