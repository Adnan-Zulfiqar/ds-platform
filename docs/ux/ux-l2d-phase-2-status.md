# UX-L2D — Phase 2 dashboard programme: status at implementation freeze

**Status: implementation complete; pending independent review and CI verification.**
Phase 2 is **not** accepted. The branch is **not** pushed, merged or deployed.

| | |
|---|---|
| Branch | `feature/ux-l2d-dashboard` (local worktree `C:\dspuxl2cdash`) |
| Stacked on | accepted Phase 1 SHA `c0092da6dde6f5dfa586436efd63a33097ed7e7c` (`fix/ci-baseline-security-pytest`, PR #7 — still draft, unmerged) |
| Frozen HEAD | recorded in the UX-L2D-07 completion report; `git merge-base HEAD c0092da6` = the Phase 1 SHA |
| Historical UX-L2C | `origin/feature/ux-l2c-live-state-clarity` (`ece8322`) — reviewed under UX-L2D-GATE-04, reference only; selected pieces adapted into -04/-05 (product page, lifecycle subset, external-link allowlist, listings query options, route-isolation spec, one E2E selector); never merged, cherry-picked or rebased |
| Backend / CI | no `backend/` or `.github/` file changed in any UX-L2D milestone |

## Milestones

| Milestone | Scope | State |
|---|---|---|
| UX-L2D-01 | Baseline audit and information architecture (`ux-l2d-01-baseline.md`) | accepted |
| UX-L2D-02 | Application shell: job-based navigation, `PageContainer` gutters, sidebar fold, drawer | accepted |
| UX-L2D-03 | Home: merchant operations page from real endpoints; no money without a currency contract | accepted |
| UX-L2D-04 | Catalogue: Drafts/Products with server search, sort, paging, URL state; `/products/[productId]` (Critical F-1 closed) | accepted |
| UX-L2D-05 | Editor lifecycle clarity: one derivation (`lib/editor-lifecycle.ts`), no "live"; M2A protocol untouched; Vitest introduced | accepted |
| UX-L2D-06 | Channels: Integrations canonical, `/stores` supporting, one vocabulary (`lib/channel-state.ts`), manual Add store removed | accepted |
| UX-L2D-07 | Cross-app hardening: journeys, accessibility, responsive matrix, vocabulary, dead UX, analytics currency neutralised, test hardening | implemented — awaiting owner acceptance, then frozen |

## What a reviewer should know

- **Vitest** (`npm run test:unit`, `vitest.config.mts`) covers the pure lifecycle and channel-state modules. It runs locally and is **not a GitHub CI gate**; the frontend CI job runs typecheck, lint, build and Playwright (chromium) only. Whether to make it a required gate is a Phase 2 acceptance decision.
- **Backend-gated Playwright specs** skip locally (no API, no seed database) and only execute in CI. The complete list, and which of them must run before Phase 2 can be accepted, is in the UX-L2D-07 report (§O) and below.
- **Known local failures**, classified in the UX-L2D-07 report: `seed-harness` ×2 (KNOWN ENVIRONMENT — Python `sqlalchemy` absent), and the timing-sensitive assertions in `publish-integrity:76` and `catalogue:98/204` (PRE-EXISTING FLAKE — made deterministic in -07). Occasional 5 s / 30 s timeouts in `auth`, `ux-l2d-shell`, `smoke` under a fully parallel full-suite run pass in isolation.

## Backend dependencies (recorded, not built)

| Dependency | Where the UI is affected | UI behaviour today |
|---|---|---|
| **Analytics currency** — `GET /analytics/dashboard` carries no currency and aggregates across stores | `/analytics` revenue tile and sales chart | Figures shown as recorded, without a symbol; the page says why (was hard-coded `$`/USD) |
| **AliExpress `configured` flag** absent from `GET /integrations/aliexpress/status` | Integrations card | "Setup unavailable" only after `POST /connect` answers the operator-facing 422 |
| **Shopify reconnect reason code** — `error` covers a revoked token and a transient failure alike, with raw `lastError` | Integrations card | "Needs attention" with Reconnect; raw text never shown |
| **Store sync flags** (`inventory/pricing/order_sync_enabled`) editable, consumed by nothing | `/stores` | not offered |
| **`Store.health_score`** — a counter, not provider health | `/stores` | not shown |
| **Publish readiness in bulk / "ready to publish" count**; **lifecycle, AI and readiness filters**; **list-row image** (thumbnails); **bulk draft actions**; **provider health scoring** | Home, Drafts, Products | not offered; no fabricated aggregate |
| Currency in the analytics payload for Home | Home | Home shows counts only (UX-L2D-03) |

## Backend-gated checks required before Phase 2 acceptance (execute in CI after push)

`product-route-isolation`, `products`, `product-optimization`, `integrations`, `shopify-oauth`, `shopify-webhook-recovery`, `ebay`, `draft-editor-real-conflict`, `draft-editor-concurrency` (live half), `draft-rich-text-description`, `shell`, `phase6-ops`, `import-history`, `csp`, `auth-g1`, `auth-provider-boundary`, `api-isolation`, plus the backend `pytest` job. Optional / environment-specific: `global-rules*`, `orders`, `privacy`, `seed-harness` (needs the seed toolchain).

## Not done in Phase 2 (deferred, by decision)

Left rail editor navigation (a scrollable strip was chosen), `AI tools` and `History` placeholder sections (removed in -07 rather than built), merging the client checklist heuristic with server blockers (F-10), toast feedback, `Select` primitive for native selects, sidebar auto-collapse at tablet widths, the editor pricing panel's "Select destination store" link target, and everything listed under backend dependencies.
