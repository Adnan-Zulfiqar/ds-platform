# Phase 9 Stage 10 — completion report

**AI Product Studio: review AI proposals one product at a time or in bulk,
and approve and publish only what the merchant confirms.**

Status: **IMPLEMENTED / AUTHOR-VERIFIED / CURSOR REVIEW PENDING.**
Production undeployed — `main` unchanged.

| | |
|---|---|
| Date | 2026-10-02 |
| Plan | [PHASE_9_STAGE_10_PLAN.md](PHASE_9_STAGE_10_PLAN.md) (PR #25, merged `81f952f`); section 0a reconciles it with the merged backend |
| Implementation PR | [#36](https://github.com/Adnan-Zulfiqar/ds-platform/pull/36), branch `feat/phase-9-stage-10-ai-studio` |
| Backend change | none (one backend unit test replaced; see below) |
| Migration | none — Alembic head stays `0036` |
| AI involvement | `StubProvider` only. No model-output quality is claimed |
| Review policy | Owner decision 2026-10-01: Cursor reviews at the end of implementation (`docs/completion/DECISIONS.md` D-001). Checkpoints DP-CR-010…014 |

## What shipped

| Area | Route / file | Plan sections |
|---|---|---|
| Single-product review | `/ai-studio/products/[productId]`, `components/ai-studio/product-review.tsx` | 5–17 |
| Comparison, quality, image evidence, readiness, Live on Shopify | `candidate-comparison.tsx`, `quality-panel.tsx`, `image-evidence.tsx`, `readiness-panel.tsx`, `live-on-shopify.tsx` | 5a, 7–10 |
| Approve dialog (G-1 copy) and legacy-version panel | `approve-dialog.tsx`, `legacy-version-panel.tsx` | 14, 26 |
| Studio home, bulk selection, run dashboard | `/ai-studio`, `studio-home.tsx`, `bulk-start-dialog.tsx`, `run-dashboard.tsx` | 18–24 |
| Contract layer | `types/api.ts` pipeline types; `services/products.ts` hooks; `lib/ai-studio/*` | 27–29 |
| Navigation | `lib/navigation.ts` (`roles` on `NavItem`), `sidebar-nav.tsx` | 5, 17 |
| Legacy Optimize retired | `ai-studio-link.tsx` replaces `optimize-product-button.tsx`; editor menu, history sheet, published summary link to Studio; `useOptimizeProduct` removed | 25 |

## Decisions taken during implementation

- **A store is preselected only when exactly one Shopify store is
  connected.** With several, the merchant chooses, so readiness is never
  composed for a store they did not mean.
- **Preview and publish get longer client timeouts** (120 s / 90 s)
  than the 30 s default: generation runs in the request and publish can
  wait 30 s for the product lock before calling Shopify. A timeout is shown
  as "outcome unknown" and re-reads state; it is never reported as a failure.
- **Sidebar section gap 16 → 12 px.** The new nav item made the list 15 px
  taller than the 1440×900 rail (measured 745 vs 730), breaking the
  UX-L2D-02 invariant. Item height is unchanged.
- **Vitest covers the pure helpers.** The plan (written before Vitest was
  added locally) said "no unit runner"; Vitest exists but is not a CI gate.
- **`useOptimizeProduct` deleted** together with the last button that used
  it, as plan section 27 allows. `POST /products/{id}/optimize` stays.
- **Stage 7's `test_frontend_has_no_pipeline_preview_type` replaced** by a
  check that the TypeScript `PipelinePreview` carries every field
  `PipelinePreviewResponse` serialises; the internal
  `pipelineCandidateVersion` key must still never reach the client.

## Verification

Author verification only. Commands ran in disposable containers
(Postgres 17, Redis 7, the real API, a production `next build`).

| Gate | Result |
|---|---|
| `npm run lint`, `npm run typecheck` | clean |
| Vitest | 90 passed (16 new) |
| `ai-product-studio.spec.ts` (retries 0) | 23 passed |
| Bulk, product-optimization (live StubProvider), review-remediation, catalogue, editor-header, shell, ux-l2d-shell (retries 0) | first run 95 passed / 2 failed (a test bug; the nav overflow) → fixed → re-run with home and hardening specs: 153 passed |
| Backend unit suite on the branch | 2221 passed; ruff, format, mypy clean |
| Full Playwright and backend suites | PR CI on #36 |

The live StubProvider case drives the real backend on a DB-seeded draft:
preview 201, approve 200, Publish disabled for synthetic text, and a direct
publish request refused with `synthetic_publish_blocked`.

## Known limitations

- A bulk run started in another browser cannot be opened after a 409
  (there is no list endpoint; plan section 40).
- Model output quality is unverifiable without a provider key.
- No real Shopify publish was exercised; publishing is covered by mocked
  responses in Playwright and by the backend publisher's own tests.
