# Release scope matrix

Every requirement of the agreed release, with its source and evidence.
Scope decision: `DECISIONS.md` D-002. Statuses use the dimensions in the
completion roadmap §17 (implementation / author verification / integration /
external / independent review).

Abbreviations: IMPL = implementation, AV = author verification,
INT = integration, IR = independent review (Cursor; `PENDING` everywhere
until Cursor actually reviews).

## Status update 2026-10-02 (supersedes the status columns below)

| Requirement IDs | Implementation | Author verification | Integration | External | Independent review |
|---|---|---|---|---|---|
| ST10-PLAN | IMPLEMENTED (plan §0a) | PASS (CI 10/10 on `fdbda28`) | MERGED_TO_DEVELOP (#25, `81f952f`) | NOT_REQUIRED | PENDING |
| ST10-A…L | IMPLEMENTED (PR #36) | PASS locally (Playwright 23 + 153, retries 0; Vitest; unit 2221); PR CI pending | NOT_MERGED | Live provider quality: BLOCKED (B-002); live Shopify publish: BLOCKED (B-003) | PENDING (DP-CR-010…014) |
| DE-6a | Covered by ST10 | as ST10 | as ST10 | — | PENDING |
| DE-6b | IMPLEMENTED (PR #38), incl. removed-image backend defect | PASS locally (backend 3462; editor specs 103) | NOT_MERGED | — | PENDING (DP-CR-021) |
| DE-7 | IMPLEMENTED (PR #37) | PASS locally (28) | NOT_MERGED | Real-Shopify retry: BLOCKED (B-003) | PENDING (DP-CR-022) |
| DE-8a | PARTIAL — Optimize ×n via ST10 bulk; bulk pricing via Global Rules; "Refresh ×n" not built | — | — | — | Scope question B-006 / D-006 |
| DE-8b | NOT_STARTED | — | — | BLOCKED (B-003, B-004) | — |
| ST11-1…4 | NOT_STARTED (follows ST10 merge) | — | — | — | — |
| REM-N5-a | Root cause found and fixed: response sent before commit (PR #35) | PASS (unit tests fail-before/pass-after; PR CI 0 flaky) | MERGED (#35, `c88a04b`) | — | PENDING (DP-CR-018) |
| REM-N5-b | Probable cause (same race via refresh-token row) — inferred, not reproduced | — | via #35 | — | OPEN until CI history confirms |
| REM-N3-p, REM-N3-u | OPEN (Next 16 / upstream) | — | — | — | — |
| REM-L1 | Local key created (D-004) | PASS | n/a | Providers not authorised (B-003) | PENDING (DP-CR-007) |
| SEC-001 | IMPLEMENTED | PASS (CI 10/10) | MERGED (#32, `7fe0a89`) | — | PENDING (DP-CR-008) |
| SEC-002 | IMPLEMENTED | PASS (CI 10/10) | MERGED (#34, `28a2c59`) | — | PENDING (DP-CR-017) |

## 1. Phase 9 Stage 10 — AI Product Studio

Source: `docs/PHASE_9_STAGE_10_PLAN.md` (PR #25, head `1dfa568`, amended
2026-10-01). Work packages ST10-A…L are the completion roadmap's grouping of
the plan's own sections; the plan's section numbers are authoritative.

| Requirement ID | Source and section | User outcome | Current implementation | Missing work | Dependencies | Acceptance test | Verification status | Review checkpoint |
|---|---|---|---|---|---|---|---|---|
| ST10-A | §5, §6.1, §17, §24 | Admin/owner opens `/ai-studio/products/[productId]` from navigation; others see no nav item and a permission panel | None (no `ai-studio` route on develop) | Route, nav item under Catalogue, role gating, not-found | #26 merged ✅ | §35 "Admin opens…", "Member/viewer fixture…", "Unknown product" | IMPL NOT_STARTED | DP-CR-010 |
| ST10-B | §27, §28, §29 | Typed client for preview/approve/publish/bulk; errors branch on `code`/reason | None | `services/products.ts` hooks, `types/api.ts` types, error mapping | ST10-A | §35 request-body assertions; §29 code table | IMPL NOT_STARTED | DP-CR-010 |
| ST10-C | §5a, §7, §8, §9, §10 | Draft / approved AI / live on Shopify / next publish source shown under their own names; comparison, quality, image evidence, readiness | None | Comparison, quality, image, readiness panels; Live on Shopify line from listings | ST10-B | §35 content-state acceptance, quality, image status cases | IMPL NOT_STARTED | DP-CR-011 |
| ST10-D | §6.2–6.5, §16 | Generate preview; URL pins `?candidate=`; reload never re-POSTs; synthetic banner | None | Preview flow, router replace, `ai_error` copy | ST10-B | §35 generate/reload/503/synthetic cases | IMPL NOT_STARTED | DP-CR-011 |
| ST10-E | §6.7–6.9, §11, §12, §13, §14 | Approve exactly this candidate with T0; adopt T1; stale and lost-response recovery | None | Approve dialog (exact G-1 copy), token state machine | ST10-D | §35 approve/stale/retry cases | IMPL NOT_STARTED | DP-CR-012 |
| ST10-F | §6.10–6.11, §15 | Publish approved candidate with the current token to the selected store | None | Publish panel, store change re-GET, result links | ST10-E | §35 publish cases; E-1 listing line | IMPL NOT_STARTED | DP-CR-012 |
| ST10-G | §27a | Product-row writes never leave an open editor with a stale token | Editor half on develop (`review-remediation.spec.ts`, I-1) | Studio must not write the editor cache; invalidate product queries | ST10-E | `review-remediation.spec.ts` stays green; §36 | IMPL PARTIAL (editor side) | DP-CR-012 |
| ST10-H | §26 | History shows Activate only where the backend allows it | `isPipelineCandidate` on develop (I-2) | AI rows link to Studio; legacy panel; invalid-metadata copy | ST10-B | §35 `not_a_pipeline_candidate` cases | IMPL PARTIAL (I-2 hides Activate) | DP-CR-013 |
| ST10-I | §18–§23 | Select ≤50 drafts+published, start run, poll, review items, cancel | None | Studio home, selection, run dashboard, item list | ST10-B | `ai-product-studio-bulk.spec.ts` (§35 bulk list) | IMPL NOT_STARTED | DP-CR-014 |
| ST10-J | §12, §13, §20 | Refresh/navigation/network loss never duplicate a mutation or orphan polling | None | Idempotency key per confirm, terminal-status polling stop | ST10-I | §35 retry/idempotency/terminal-poll cases | IMPL NOT_STARTED | DP-CR-014 |
| ST10-K | §4, §25 | No primary UI path silently activates AI text | `OptimizeProductButton`, "Improve with AI tools" still present | Remove from primary UI; keep backend route | ST10-A | §36 regression matrix (`catalogue`, `editor-header`, `product-optimization` specs) | IMPL NOT_STARTED | DP-CR-013 |
| ST10-L | §30, §31, §32 | Responsive, keyboard-operable, readable states | None | Mobile cards, `aria-pressed` toggles, focus management | all | §35 keyboard and mobile cases | IMPL NOT_STARTED | DP-CR-014 |
| ST10-PLAN | PR #25 | Plan reconciled with merged contracts | Plan amended 2026-10-01 | Rebase on develop; remove "do not start" gate per D-001; verify every contract against code | #26 merged ✅ | Contract map (AUT-04) | AV NOT_RUN | DP-CR-009 |

## 2. Phase 9 Stage 11 — "Docs, gates, tag — Completion report"

Source: `docs/PHASE_9_PLAN.md` §3 row 11 and §7 ("`phase-9-complete`
will not be created until every stage in §3 is done and its verification
executed"). No separate Stage 11 plan exists; the deliverables below are
the conservative reading of that row.

| Requirement ID | Source | Outcome | Current | Missing | Dependencies | Acceptance | Status | Checkpoint |
|---|---|---|---|---|---|---|---|---|
| ST11-1 | PHASE_9_PLAN §3, §6 | `docs/PHASE_9_COMPLETION.md`: what shipped per stage, verification table incl. the "model output quality cannot be verified" gap | None | Write | ST10 complete | Report exists and matches evidence | NOT_STARTED | DP-CR-020 |
| ST11-2 | §3, §7 | Full gates on the integrated tree | — | Backend + frontend + Playwright on the candidate | ST10 | Exact-SHA results | NOT_STARTED | DP-CR-020 |
| ST11-3 | §7, CLAUDE.md §10 | Tag `phase-9-complete` | — | Tag only after ST11-1/2 | ST11-2 | Tag points at the verified SHA | NOT_STARTED | DP-CR-020 |
| ST11-4 | CLAUDE.md §9 | CHANGELOG and roadmap updated | Partial (through Stage 9) | Stage 10/11 entries | ST10 | Docs match | NOT_STARTED | DP-CR-020 |

## 3. Draft Editor stages 6–8

Source: `docs/DRAFT_PRODUCT_EDITOR_PLAN.md` "Stage progress" and
"Remaining (Stages 5–8)"; `PROJECT_ROADMAP.md` Product Workspace V2 table.
The source gives one line per stage; detailed requirements must be derived
from the current code before implementation (AUT-06).

| Requirement ID | Source | Outcome | Current (per source) | Missing work | Dependencies | Acceptance | Status | Checkpoint |
|---|---|---|---|---|---|---|---|---|
| DE-6a | Stage 6 "AI Studio proposals" | AI proposals reviewed before they affect a draft | Delivered by Phase 9 Stage 10 (same workflow; plan §5 links the editor to Studio) | Covered by ST10 rows | ST10 | ST10 tests | NOT_STARTED | DP-CR-010…014 |
| DE-6b | Stage 6 "full readiness" | Readiness shows every server-enforced publish blocker | "basic readiness sidebar exists" | Inspect: compare sidebar items with server publish validation | — | Each server blocker has a sidebar item (test) | NOT_VERIFIED — inspect | DP-CR-021 |
| DE-7 | Stage 7 "Idempotent publish UX polish" | Repeated clicks/retries never duplicate a listing; status is clear | "Partial — Publish panel calls existing Shopify publish"; UX-L2B server publish authority on develop | Inspect against UX-L2B and E-1 | — | Double-click/retry Playwright case | NOT_VERIFIED — inspect | DP-CR-022 |
| DE-8a | Stage 8 "Bulk tools" | Bulk actions on drafts | Pending | Define from existing draft list actions; no invented actions | — | Playwright | NOT_VERIFIED — inspect | DP-CR-023 |
| DE-8b | Stage 8 "live E2E verification", "live AliExpress → Shopify E2E" | Import → edit → publish verified on real providers | Pending | Needs live AliExpress OAuth and a designated Shopify test store | External (B-003, B-004) | Live run evidence | EXTERNAL BLOCKED; fixture E2E possible | DP-CR-023 |

## 4. Open remediation findings

Source: `docs/REVIEW_REMEDIATION_STAGE_5_9.md` (ledger), status 2026-10-01.

| Requirement ID | Finding | Current | Missing | Status | Checkpoint |
|---|---|---|---|---|---|
| REM-N5-a | `global-rules-impact.spec.ts` flakes (:300 on run 36855424195, :635 on run 36877363551) | Not reproduced locally (5×, retries off) | Root-cause investigation under CI-like load | OPEN | DP-CR-015 |
| REM-N5-b | `draft-editor-real-conflict.spec.ts:211` flake | Not reproduced | Same | OPEN | DP-CR-015 |
| REM-N3-p | Bundled `postcss` in `next` 15.5 | Needs Next 16 major | Assess Next 16 upgrade in an isolated change | OPEN (release blocker) | DP-CR-016 |
| REM-N3-u | Postponed Next.js upstream fixes | No public detail | Watch | OPEN (upstream) | DP-CR-016 |
| REM-L1 | `SECURITY_ENCRYPTION_KEYS` missing locally | Local-only key created 2026-10-01 (D-004); round trip verified | Provider-level verification (CONFIG_LOADED etc.) | LOCAL RESOLVED; providers see AUT-03 | DP-CR-007 |
| SEC-001 | Nested `.env` in Docker build context | PR #32 | Merge after CI | FIXED ON BRANCH | DP-CR-008 |
| SEC-002 | `pyjwt` / `urllib3` advisories | `fix/backend-pyjwt-urllib3` | PR, CI, merge | FIXED ON BRANCH | DP-CR-017 |

Every other ledger finding (A-1…K-5, N-1…N-4) is FIXED, VERIFIED/NO CHANGE
or RESOLVED per the ledger and integrated into `develop` via #26–#31;
independent review of those fixes is `PENDING`.

## 5. Future / out of release

Source for all rows: `PROJECT_ROADMAP.md` "Later phases" — "Not scheduled
… Nothing here is committed to a phase number."

| Area | Additional source | Status |
|---|---|---|
| eBay EBAY-C2…C6 (policies, listing, sync, orders, hardening) | `docs/ebay/MASTER_EBAY_ROADMAP.md` "not started" | FUTURE |
| WooCommerce / Etsy / TikTok channels | — | FUTURE |
| Shopify fulfilment push | — | FUTURE |
| Real FX M24B/M24C | `docs/TECHNICAL_DEBT.md` | FUTURE |
| Subscription billing, team management, admin panel, outbound email | — | FUTURE |
| Live model providers (OpenAI/Anthropic/Gemini) | `PHASE_9_PLAN.md` §8 Stage 1 decision | FUTURE (no key; B-002) |

## Status update 2026-10-02 (second pass; supersedes rows above where they differ)

| Requirement IDs | Implementation | Author verification | Integration | External | Independent review |
|---|---|---|---|---|---|
| ST10-A…L | IMPLEMENTED | PASS (PR CI 36949638454; browser on the local candidate) | MERGED (#36, `fbadddb`) | Live provider / Shopify: BLOCKED (B-002, B-003) | PENDING (DP-CR-010…014) |
| ST11 | Report written; gates on the candidate; tag deferred (D-008) | PASS (candidate CI) | MERGED (#41) | — | PENDING |
| DE-6b, DE-7 | IMPLEMENTED | PASS | MERGED (#38, #37) | Real-Shopify retry: BLOCKED (B-003) | PENDING |
| DE-8a | Bulk half of Stage 8 met by AI Studio bulk + Global Rules; "Refresh ×n", "Publish Selected", scheduling → FUTURE (D-010). **Stage 8 as a whole is NOT complete** while DE-8b is blocked | — | — | — | Classification consistent with sources; Stage 8 not complete (IR-06) |
| DE-8b | NOT STARTED | — | — | BLOCKED (B-003, B-004) | — |
| REM-N3-p | FIXED (Next 16.3.8) | PASS | MERGED (#40) | — | PENDING |
| D-009 lint / proxy migration | FIXED | PASS (769 passed, retries 0) | MERGED (#42, `9a62b9e`) | — | PENDING (DP-CR-019) |
| REM-N5-a | Root cause fixed (#35) | PASS | MERGED | — | PENDING (DP-CR-018) |
| REM-N5-b (flake A) + Studio selection flake (B) | A: mechanism reproduced deterministically 2026-10-03, closed by author pending independent confirmation; B: OPEN — cause unproven (DP-CR-015) | 70/70 and 35/35, retries 0 | — | — | Keep open; green repeats are not a cause (IR-05) |
| SEC-001 existing images | Assessed; task-owned removed; clean replacements | PASS | n/a | Owner rebuild (B-007) | PENDING (DP-CR-024) |
