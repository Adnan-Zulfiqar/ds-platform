# DP-CR-010…014 — Phase 9 Stage 10 AI Product Studio (PR #36)

One file for the five Stage 10 checkpoints; each section is one review unit.
Report: `docs/PHASE_9_STAGE_10_COMPLETION.md`. Plan: `docs/PHASE_9_STAGE_10_PLAN.md` (§0a wins over later text).

- **Base / head:** develop `df0e41f` → PR [#36](https://github.com/Adnan-Zulfiqar/ds-platform/pull/36) (`feat/phase-9-stage-10-ai-studio`).
- **Backend / schema:** none. One backend unit test replaced (DP-CR-013).
- **Author verification:** lint, typecheck clean; Vitest 90 passed; Playwright (retries 0) — studio spec 23 passed; bulk + regression + shell specs 153 passed after two fixes; backend unit suite 2221 passed. Full suites: PR CI.
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md)) (all five units).

## DP-CR-010 — Route, access, contract layer (ST10-A, ST10-B)

- `types/api.ts` pipeline types (checked field by field against `backend/app/schemas/product.py` and `pipeline_bulk.py`); `services/products.ts` hooks; `lib/ai-studio/{tokens,errors,bulk,text}.ts`.
- Role gate `StudioAccess`: owner/admin render the Studio; others see "AI Studio is available to owners and admins." and **no pipeline request** is sent (asserted). Nav item carries `roles: ["owner", "admin"]`.
- **Inspect:** the candidate query key includes `storeId`; invalidation on the hooks (not per-call callbacks); preview/publish timeouts (120/90 s) and the "outcome unknown" copy.

## DP-CR-011 — Comparison and preview (ST10-C, ST10-D)

- Current draft vs AI candidate; candidate first below `lg`. Both descriptions as text (supplier through `stripHtml`, candidate `whitespace-pre-wrap`); a test injects `<img onerror>` and asserts no element and no script effect.
- Generate → `router.replace(?candidate=…)`; reload GETs and never re-POSTs (asserted). `ai_error` 503 shows copy + requestId, no auto-retry (asserted).
- Scores labelled against the original baseline; no "confidence"/"current score" (asserted). Image rows by outer status, unknown statuses safe (asserted).
- **Inspect:** `stripHtml` is a display helper, not a sanitiser — the server sanitises; nothing here uses `dangerouslySetInnerHTML`.

## DP-CR-012 — Approve, publish, tokens (ST10-E, ST10-F, ST10-G)

- Approve body exactly `{ expectedUpdatedAt: T0 }` on the version URL; publish body exactly `{ storeId, expectedUpdatedAt }`. Publish enabled only by a successful GET of the active candidate for the selected store; a later server token (T2) wins over the approve response's T1 (asserted).
- Stale blocker → approval disabled, one explicit "Generate fresh preview"; approve 409 stale → same state, not retried; a lost approve response → retry with the same T0, adopt the returned token (all asserted).
- Live on Shopify from the listing's `contentSource`/`contentVersionId`, refetched after publish (asserted).
- **Inspect:** `product-review.tsx` `canApprove` / `canPublish`; `lib/ai-studio/tokens.ts`.

## DP-CR-013 — History, legacy versions, retired Optimize (ST10-H, ST10-K)

- History: pipeline rows link "Review in AI Studio"; legacy rows keep Activate. GET 422 `not_a_pipeline_candidate` → legacy panel; Activate refused with the same reason stops without a loop (asserted).
- `OptimizeProductButton` → `AiStudioLink`; editor menu "Open AI Studio" (a link; nothing written from the editor); published summary link. `useOptimizeProduct` deleted; `POST /optimize` kept.
- Stage 7 test `test_frontend_has_no_pipeline_preview_type` (a scope guard for Stage 7) replaced by a wire-contract check: every `PipelinePreviewResponse` field must exist on the TS type; `pipelineCandidateVersion` must never appear. It failed PR CI before the replacement — the author had not run the backend suite on a frontend-only change.
- **Inspect:** that removing the editor's Optimize path removed no I-1 protection still needed (Activate keeps it).

## DP-CR-014 — Bulk runs, recovery, UX (ST10-I, ST10-J, ST10-L)

- One capped set (50) across Drafts/Published, kept across pages and views; `size` not `pageSize`; one idempotency key per snapshot, reused on retry (asserted identical bodies); 5 s polling stops at a terminal status (asserted no further GET); item `state` decides Open review; cooperative cancel shows "Cancelling…"; 409 active run reported once, not retried.
- `aria-pressed` toggles, no `role="tab"` (asserted); mobile width without horizontal scroll (asserted).
- Nav gap 16 → 12 px to keep the 1440×900 rail scroll-free (measured 745 vs 730).
- **Inspect:** session-storage shortcut for the active run is read after mount (no hydration mismatch); both lists are fetched while only one is shown (two requests per page — accepted for simplicity).
