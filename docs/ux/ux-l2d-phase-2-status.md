# UX-L2D — Phase 2 dashboard programme: status

**Status: merged into `develop` (PR #9, merge commit `b52b223`, 2026-09-17). Not deployed to production.**

## Integration record (2026-09-17)

| Step | Evidence |
|---|---|
| Original frozen review SHA | `3f1ede8578d30125d3d81c6f227be95e4d01480c` (tree `0aac7f360d2e9d2697a52f63e3e0f5beb3ba8dff`); 25 commits on the Phase 1 base |
| Independent review of the frozen SHA | `PASS — FROZEN PHASE 2 SHA APPROVED FOR PR/CI VERIFICATION` (verdict recorded in the PR #9 body); no Blocker/High findings |
| Remediation | two commits after the frozen SHA — `2351bcc` (catalogue phone-card test identity) and `41c152c` (channels webhook copy; Analytics heading assertions) — reviewed as a delta: remediation delta review PASS (recorded in the merge commit message) |
| Final accepted head | `41c152c4ca49a099951f5fd2dc53a3578e01ea62` (tree `489366a927363fcb029f8caabd1b7102ee4d8011`); 27 commits on `c0092da6`; `git merge-base` with the Phase 1 SHA = the Phase 1 SHA |
| CI on the accepted head | run 35109481727 (`pull_request`, 2026-09-16): 10/10 jobs — ruff, ruff format, mypy, pytest 2984 passed; typecheck, lint, build; Docker images + compose config + compose smoke; Celery broker; Playwright 722 passed / 0 failed / 9 skipped |
| Phase 1 prerequisite | PR #7 (`fix/ci-baseline-security-pytest`, `c0092da6`) merged into `develop` 2026-09-16, merge commit `5e21927`; CI on that merge commit run 35164379004 10/10 |
| Merge into `develop` | PR #9 merged by the owner 2026-09-17 06:47 UTC with a merge commit, `b52b223195b36972cad2d2fd5f1392ef864ea71f` (parents `5e21927`, `41c152c`), so every reviewed SHA stays reachable |
| CI on the merge commit | run 35191388324 (`push` to `develop`): 10/10 jobs; pytest 2984 passed; Playwright 722 passed / 0 failed / 9 skipped |
| Formal GitHub review submissions | none on PR #7 or PR #9 — the review evidence is the verdicts recorded above (PR #9 body, merge commit message) and the owner-held reviewer reports, not a GitHub review |
| Production | **not deployed.** `main` (`3ce66d4`, 2026-09-13) contains neither `c0092da6` nor `41c152c` |

Open after the merge: whether Vitest becomes a CI gate (still local only); the backend dependencies below; the Celery exclusive-pidbox drill carried from Phase 1 (safe staging, outside UX-L2D).

## State at implementation freeze (historical, 2026-09-16)

The sections below record the branch as it stood when implementation froze, before the review and merge above. Statements about pushed/merged state in them are superseded by the integration record.

| | |
|---|---|
| Branch | `feature/ux-l2d-dashboard` (local worktree `C:\dspuxl2cdash`); pushed for review, merged via PR #9 |
| Stacked on | accepted Phase 1 SHA `c0092da6dde6f5dfa586436efd63a33097ed7e7c` (`fix/ci-baseline-security-pytest`, PR #7 — merged 2026-09-16) |
| Frozen review HEAD | `3f1ede8` (tree `0aac7f36`); final accepted head after remediation `41c152c` (tree `489366a9`); `git merge-base HEAD c0092da6` = the Phase 1 SHA |
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
| UX-L2D-07 | Cross-app hardening: journeys, accessibility, responsive matrix, vocabulary, dead UX, analytics currency neutralised, test hardening | accepted — frozen at `3f1ede8`, remediated to `41c152c`, merged |

## What a reviewer should know

- **Vitest** (`npm run test:unit`, `vitest.config.mts`) covers the pure lifecycle and channel-state modules. It runs locally and is **not a GitHub CI gate**; the frontend CI job runs typecheck, lint, build and Playwright (chromium) only. Whether to make it a required gate is a post-merge decision still open.
- **Backend-gated Playwright specs** skip locally (no API, no seed database) and only execute in CI. The complete list is in the UX-L2D-07 report (§O) and below; they ran in CI runs 35109481727 and 35191388324.
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

## Backend-gated checks required before Phase 2 acceptance (executed in CI: runs 35109481727 on `41c152c` and 35191388324 on `b52b223`, Playwright 722 passed / 0 failed / 9 skipped)

`product-route-isolation`, `products`, `product-optimization`, `integrations`, `shopify-oauth`, `shopify-webhook-recovery`, `ebay`, `draft-editor-real-conflict`, `draft-editor-concurrency` (live half), `draft-rich-text-description`, `shell`, `phase6-ops`, `import-history`, `csp`, `auth-g1`, `auth-provider-boundary`, `api-isolation`, plus the backend `pytest` job. Optional / environment-specific: `global-rules*`, `orders`, `privacy`, `seed-harness` (needs the seed toolchain).

## Not done in Phase 2 (deferred, by decision)

Left rail editor navigation (a scrollable strip was chosen), `AI tools` and `History` placeholder sections (removed in -07 rather than built), merging the client checklist heuristic with server blockers (F-10), toast feedback, `Select` primitive for native selects, sidebar auto-collapse at tablet widths, the editor pricing panel's "Select destination store" link target, and everything listed under backend dependencies.
