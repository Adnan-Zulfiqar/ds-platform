# Claude return review — remediation ledger (Stages 5–9)

Review: "DropPilot full Claude return review: remediation required"
(2026-09-30), against `origin/develop` `72e76921` and Stage 10 plan head
`ef125ed3`. Remediation branch: `fix/stage5-9-review-remediation`, cut
from `72e76921`. The Stage 10 plan corrections live on PR #25's branch,
`docs/phase-9-stage-10-plan`.

**Every finding from the review is listed here. None was dropped.** Status
is one of `FIXED`, `EXTERNAL BLOCKER`, `VERIFIED/NO CHANGE`, `DEFERRED`.

Nothing here is merged or deployed, and nothing has been independently
accepted: everything below is **author verification** (Claude wrote this
remediation). As of 2026-10-01 GitHub Actions runs again; the CI results
for head `47fd036` are recorded in "CI results" below.

## How the work was verified

- Backend: disposable `postgres:17-alpine` + `redis:7-alpine` containers on
  a private Docker network; the worktree mounted read-only into a container
  built from the backend image plus the hash-locked `requirements/dev.txt`.
  The shared development database and the developer's `.env` files were not
  touched.
- Migrations: upgrade → downgrade → upgrade, and `alembic check`, on a
  throwaway database dropped afterwards.
- Frontend: lint, typecheck and production build; Playwright in a Linux
  container that mirrors the CI job (same env values, API on the local port
  range 8100 instead of 8000).

## Findings

| ID | Sev | Status | What changed | Tests proving it | Commit |
|---|---|---|---|---|---|
| E-1 | MEDIUM | FIXED | `store_listings.content_source` + `content_version_id` (migration `0035`, composite `(tenant_id, product_id, version)` FK, source/version check). `publish_product` re-sends a live AI version by default for every caller (editor HTTP, Celery, retries); only `replaceAiContent` from an explicit, confirmed merchant choice sends the draft; an unreadable live version fails closed (409 `published_ai_content_unavailable`). Responses and listing reads expose the source. Editor shows the state and the confirmation. | `tests/integration/test_listing_content_source.py` (13); `frontend/tests/e2e/review-remediation.spec.ts` E-1 block (4) | `4df816c`, `d7dc574` |
| G-1 | MEDIUM | FIXED (plan + test) | Stage 10 plan Approve copy corrected and the four states (draft / approved AI / published / next publish source) defined — on PR #25. Backend acceptance test pins that approval changes only the AI cache. | `TestApprovalAloneChangesNothingOnShopify`; existing `test_approve_activates_the_exact_candidate` | `9b45c72`; plan commit on PR #25 |
| I-1 | MEDIUM | FIXED | Optimize/Activate refuse to start on a dirty or conflicted editor; invalidate the draft cache; on success re-baseline the editor from the returned product when nothing was edited meanwhile; edits made mid-request get a notice and the server's 409 review, never a silent re-base. Activation callback moved to `useMutation` options (a per-call callback did not run once the sheet closed — found by these tests). | review-remediation.spec.ts I-1 block (6) | `d7dc574` |
| L-1 | MEDIUM | EXTERNAL BLOCKER (partly resolved) | None — environment, not code. Rechecked 2026-10-01: provider app keys now SET; `SECURITY_ENCRYPTION_KEYS` still EMPTY. See below. | Name-only SET/EMPTY check | — |
| J-1 | HIGH | RESOLVED (external) | GitHub Actions starts again from 2026-10-01. No workaround was ever added. | CI run 36855448085 | — |
| A-1 | LOW | FIXED | `CLAUDE.md` §4 lists the actual unscoped data-access classes by purpose instead of "only two". | doc | docs commit |
| A-2 | LOW | FIXED | All bulk SQL moved to `app/repositories/pipeline_bulk.py`; the cross-tenant sweep is a separate `PipelineBulkRunSweep` returning ids only. Run-row lock is now `FOR NO KEY UPDATE` (still serialises every writer; no longer blocks FK `KEY SHARE`). | `tests/unit/test_pipeline_bulk_repository_scoping.py` (20); all Stage 9 integration suites | `7b7985d`, `ebb841f` |
| B-1 | LOW | FIXED | `next_version_number` states the tenant predicate. | scoping test above | `7b7985d` |
| B-2 | LOW | FIXED | Composite `(tenant_id, store_id)` FK on runs (migration `0036`, `uq_stores_tenant_id_id`). | `test_bulk_run_referential_integrity.py::TestRunStoreBelongsToTheRunTenant` (failed before the fix) | `7b7985d` |
| B-3 | LOW | FIXED | NAT64 `64:ff9b::/96` judged by its embedded IPv4; IPv4-compatible `::/96` refused; 30 s whole-fetch deadline; resolver off the event loop. Every prior control kept. | `tests/unit/test_image_fetch_residuals.py` (15) + unchanged `test_image_fetch.py` | `a650c98` |
| C-1 | LOW | FIXED | Shopify state, Shopify install ticket and AliExpress state consumed with `core.redis.take_once` (MULTI/EXEC). Not GETDEL: the supported baseline is Redis 3.0.504. | `tests/unit/test_oauth_state_single_use.py` (concurrency: 1 of 25 wins) | `a650c98` |
| D-1 | LOW | FIXED | Migration `0036` sets bulk `created_at`/`updated_at` NOT NULL and adds the declared indexes. `alembic check`: 0 items on touched tables (43 → 35 overall; the rest are historic, out of scope). | migration round trip + `alembic check` | `7b7985d` |
| D-2 | LOW | VERIFIED/NO CHANGE | A hard `DELETE FROM tenants` cascades through runs, items, products, versions and stores with the RESTRICT FKs; a referenced product or store still cannot be deleted alone. Now regression-tested. | `test_bulk_run_referential_integrity.py` (passed before and after) | `7b7985d` |
| G-2 | LOW | FIXED | The first preview's snapshot bookkeeping keeps `Product.updated_at`; real activations still move it. | `tests/integration/test_preview_token_stability.py` (2 of 3 fail without the fix) | `1e79fd9` |
| G-3 | LOW | FIXED | Preview, bulk start and legacy optimize accept only the five Studio tones. | `tests/unit/test_review_remediation_schemas.py` | `6fd2d67` |
| H-1 | LOW | FIXED | The sweep republishes a pending run at most once per 15 minutes (`enqueued_at`). | `test_pipeline_bulk_remediation.py::TestRepublishWindow` | `b7de9f2` |
| H-2 | LOW | FIXED | Cancel tries `NOWAIT` in a savepoint; if a worker holds the run it records `pipeline_bulk_run_cancel_requests` and returns at once (`cancelRequestedAt`); the worker honours it at the next item boundary, `claim` honours an orphaned one. | `test_pipeline_bulk_fencing.py` (rewritten to the new contract), `TestCancellationReleasesTheLease` | `b7de9f2` |
| H-3 | LOW | FIXED | Task limits 1500 s / 1680 s, under RabbitMQ's default 30-minute `consumer_timeout`; on the soft limit after progress the run is handed back to pending under its fence and a continuation is published. No-progress slices still spend a retry. | `TestTimeLimitContinuation` | `b7de9f2` |
| H-4 | LOW | FIXED | Cancel clears the lease; the unreachable `finalize` branch is removed. | `test_cancelling_a_running_run_clears_its_lease` | `b7de9f2` |
| H-5 | LOW | FIXED | Unexpected per-item errors fail that item (`unexpected_error`, type only — never the message); three attempts fail an item on its own. | `TestUnexpectedItemFailure` | `b7de9f2` |
| I-2 | LOW | FIXED | `ProductVersionRead.isPipelineCandidate`; history shows no Activate for those rows. | `test_review_remediation_schemas.py`; I-2 Playwright case | `6fd2d67`, `d7dc574` |
| J-3 | LOW | FIXED | Mobile overflow check polls for 5 s instead of one sample. | the test itself (needs a live backend; not run locally — see gates) | `5126802` |
| J-4 | LOW | FIXED | Beat overrides the worker-ping HEALTHCHECK with a schedule-file freshness check; nginx probes 127.0.0.1. Both verified against the real images. | `tests/unit/test_compose_health_remediation.py` | `dc0315d` |
| K-1 | LOW | FIXED | Roadmap, changelog and Phase 9 plan show Stage 9 merged and the review done. | doc | docs commit |
| K-2 | LOW | FIXED | Stage 9 completion carries the CI-authoritative count (3367). | doc | docs commit |
| K-3 | LOW | FIXED | `CLAUDE.md` and `docs/Contributing.md` describe the develop/main split; the stale "reserved products module" example is corrected. | doc | docs commit |
| K-4 | LOW | FIXED | Beat writes its schedule to `/tmp`; `celerybeat-schedule*` ignored. The three files already in the main checkout were **not** deleted: the running beat container still writes them (it predates this change); they disappear from `git status` via the ignore rule and stop being created once beat is recreated. | compose test | `dc0315d` |
| B-4, B-5, C-2, D-3, E-2, F-1, G-4, G-5, H-6 | INFO | VERIFIED/NO CHANGE | Re-checked; nothing to change. G-4 (failed prompt-execution audit rows roll back with the preview) and H-6 (broker harness covers duplicate delivery after completion; concurrency is covered by the fencing suite) remain as documented behaviour. | — | — |

### Found during remediation

| ID | Sev | Status | Detail | Commit |
|---|---|---|---|---|
| N-1 | HIGH (deploy) | FIXED | Lightsail beat had no `--schedule` on a read-only root filesystem and died at startup with `OSError 30` — no scheduled task (reconcilers, order sync) would have run. Reproduced against the worker image with `--read-only`. | `dc0315d` |
| N-2 | MEDIUM | FIXED | Closing the history sheet mid-activation skipped the token adoption (React Query per-call callbacks do not run after unmount). | `d7dc574` |
| N-3 | HIGH (deploy) | FIX IN SEPARATE PR #28 (unmerged) | `next@15.1.6` was affected by CVE-2025-66478. The current advisory database shows the 15.1 line unmaintained and two critical 2026-09-08 RCEs fixed only from 15.5.24, so #28 moves to `next` 15.5.27 / `react` 19.0.8, plus a no-JS rendering fix the upgrade needed. Not mixed into this PR. **Blocks deployment until #28 merges.** | PR #28 |
| N-4 | MEDIUM | FIXED in PR #27 (cherry-picked here as `47fd036`) | CI installed `-e ".[dev]"` from pyproject ranges and picked up SQLAlchemy 2.1.1 (lock: 2.0.52): 70 mypy errors on unchanged code (runs 36802573335, 36802617578). CI now installs the hash lock with `--no-deps`. | `test_ci_installs_from_lock.py` | `78c7f06` / `47fd036` |
| N-5 | LOW | OPEN (develop baseline) | On run 36855424195 (develop + #27) six Playwright tests passed only on retry: `global-rules.spec.ts` (:215, :429, :566, :603), `global-rules-impact.spec.ts:300`, `draft-editor-real-conflict.spec.ts:211`. None flaked on this branch's run. Owner: follow-up after #26/#28 merge. | — | — |

## External blockers (owner action required)

**J-1 — GitHub Actions (resolved 2026-10-01).** Runs 35721345516 and
35724967705 (PR #25, head `ef125ed`) were refused for billing. From
2026-10-01 jobs start again; no workaround was ever added and no check was
weakened.

**L-1 — local integration credentials (rechecked 2026-10-01, names only).**
The root `.env` changed on 2026-10-01 and the backend and worker were
restarted: `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `ALIEXPRESS_APP_KEY`,
`ALIEXPRESS_APP_SECRET` are now **SET** in both containers.
`SECURITY_ENCRYPTION_KEYS` is still **EMPTY** everywhere (root `.env`,
`backend/.env`, both containers), so connecting or reading any provider
credential still fails closed with "Credential encryption is not configured
on this server." No value was read or printed, no key was generated and no
environment file was edited. **Owner:** repository owner — restore the
**same historic Fernet key(s)** from the secure backup; a new key would make
every existing encrypted connection row undecryptable. `backend/.env` (read
by the bind-mounted app) is still the example template and is shadowed by
the container environment; reconcile it when restoring.

## CI results (GitHub Actions)

| PR / head | Run | Result |
|---|---|---|
| #26 `47fd036` (this branch + lock fix) | 36855448085 | **10/10 success.** Backend: ruff, format (468 files), mypy (236 files), full pytest **3460 passed, 0 failed**. Celery broker harness, Docker builds, compose config and smoke all success. Playwright **733 passed, 9 skipped, 0 flaky** |
| #27 `78c7f06` (develop + lock fix) | 36855424195 | 10/10 success; Playwright 716 passed, 6 flaky (N-5), 9 skipped |

The 8 local failures recorded below were environment-only; on CI's Linux
checkout the same suite has none.

## Reviewer handoff (independent acceptance still required)

Author: Claude. Not independently accepted. To accept PR #26:

1. **Range:** `git log 72e7692..1f37ff1` — 16 commits (15 code/test/doc commits to `47fd036`, plus this ledger update). Merge order: #27
   (identical CI commit) first, then #26, then #28; #25 only after #26.
2. **Highest-risk areas to read line by line:**
   - `integrations/shopify/sync.py::_resolve_listing_content` and migration
     `0035`: the E-1 publish-source contract (the default must preserve live
     AI text for every caller; only `replaceAiContent` may replace it).
   - `services/pipeline_bulk.py` + `repositories/pipeline_bulk.py`: the
     `FOR NO KEY UPDATE` lock change, `NOWAIT` cancel in a savepoint, the
     cancel-request table, time-slice continuation in `tasks/ai.py`.
   - `services/product_optimization.py::_ensure_original_snapshot`: the
     deliberate `updated_at` preservation (G-2).
   - `frontend/components/drafts/draft-product-editor.tsx`: the new
     `hydrateFromServer` transition 3 and `adoptAfterProductAction`.
   - `ai/image_fetch.py`: NAT64 handling must not weaken any existing check.
3. **Evidence to re-run:** `tests/integration/test_listing_content_source.py`,
   `test_pipeline_bulk_*.py`, `test_preview_token_stability.py`,
   `tests/unit/test_image_fetch*.py`, `test_oauth_state_single_use.py`,
   `frontend/tests/e2e/review-remediation.spec.ts`.
4. **Explicit judgement calls for the reviewer:** H-2's `running` +
   `cancelRequestedAt` response shape; G-2's choice to not move the token
   for snapshot bookkeeping; the removal of finalize's CANCELLED branch.

## Local gate results (code head `6e7d25e`)

Isolated containers only. **Not a substitute for CI, which did not run.**

| Gate | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | 467 files already formatted |
| `mypy app` | Success, 236 source files |
| `alembic heads` | `0036` (single head) |
| Migration round trip | 0036 → 0034 → 0032 → 0036 pass |
| `alembic check` | 0 items on any table this branch touches; 35 historic items elsewhere (were 43) |
| Full pytest (whole repo, disposable Postgres 17 + Redis 7) | **3449 passed, 8 failed, 0 skipped**, 331 s. All 8 are environment-only and pass on the host: 7 exec-bit tests need `git` (absent in the container), 1 home-directory test fails as root. Backup/restore drill ran (pg 17 client tools installed) and passed |
| Committed-secret checker | PASS |
| Celery broker harness (`verify_celery_broker.py`, `verify_pipeline_bulk_broker.py`, real RabbitMQ 4 + worker) | both PASSED |
| Frontend lint / typecheck | pass / pass |
| Frontend production build | pass |
| Playwright, full suite, chromium, CI-equivalent container | **733 passed, 9 skipped, 0 failed, 0 flaky**, 17.6 min (CI baseline on `72e7692`: 721 passed, 1 flaky, 9 skipped) |
| Playwright `review-remediation.spec.ts`, `--repeat-each=3` | 33/33 |
| `docker compose config -q` (local; Lightsail with placeholder values) | pass / pass |
| Health checks against real images | new beat check PASS (old worker-ping FAIL); nginx `localhost` → `::1` confirmed |

## Status update 2026-10-01 (second pass)

### PR map and integration order

| Order | PR | Branch | Head | Purpose |
|---|---|---|---|---|
| 1 | #27 | `ci/install-backend-from-lock` | `78c7f06` | N-4: CI installs the hash lock |
| 2 | #26 | `fix/stage5-9-review-remediation` | (this head) | Stage 5–9 remediation; carries #27's commit as `47fd036` (same change, merges cleanly after #27) |
| 3 | #28 | `security/next-react-advisories` | `ce0e3e2` | N-3: next 15.5.27, react 19.0.8, no-JS fix |
| 4 | #29 | `security/axios-1-20` | `02f7105` | axios 1.20.0 |
| 5 | #30 | `security/tiptap-and-dev-transitives` | `23daed7` | tiptap 3.31.4, js-yaml, brace-expansion; #29 merged in to pre-resolve their adjacent `package.json` lines |
| 6 | #31 | `fix/global-rules-list-refresh` | `4ee7dde` | N-5: rule dialog waits for the list refetch |
| 7 | #25 | `docs/phase-9-stage-10-plan` | `1dfa568` | Stage 10 plan; rebase on `develop` after #26 |

All six branches (#27 → #31) merge onto `develop` 72e7692 in this order
**without conflicts** (verified with a throwaway local merge). #28–#31 each
carry #27's commit; it drops out of their diffs once #27 merges.

### Dependency-security assessment (frontend, actual installed graph)

| Package | Installed (develop) | Exposure | Advisories | Fixed by |
|---|---|---|---|---|
| `next` | 15.1.6 | production | GHSA-9qr9-h5gf-34mp (CVE-2025-66478, critical, 2025-12-03) and 32 later `next` advisories incl. GHSA-2xp9-vwfh-vxw4 and GHSA-p293-qw3h-jr36 (critical RCEs, 2026-09-08, fixed ≥15.5.24) | #28 → 15.5.27 |
| `react`, `react-dom` | 19.0.0 | production | react-server-dom RSC advisories through GHSA-wx67-qw84-cm4g (fixed 19.0.8) | #28 → 19.0.8 |
| `sharp` (via `next`) | per next 15.1.6 | production (image optimisation, unused here) | GHSA-f88m-g3jw-g9cj, GHSA-rgj7-g3m4-5g8c | #28 (newer next) |
| `axios` | 1.19.0 (direct) | production | 12 advisories, 7 high (see #29) | #29 → 1.20.0 |
| `@tiptap/*` | 3.30.1 | production (description editor) | GHSA-j95f-988m-3j2f (high), GHSA-cp6q-959q-f8rh | #30 → 3.31.4 |
| `js-yaml` | 4.3.0 | dev/CI only (eslintrc) | GHSA-5p4m-2wfm-xmqj, GHSA-2883-xcg3-v3hh | #30 → 4.3.2 |
| `brace-expansion` | 1.1.18, 5.0.9 | dev/CI only (minimatch) | GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p | #30 → 1.1.21 / 5.0.12 |
| `postcss` bundled in `next` | 8.4.31 (exact pin in next 15.5.27) | build-time only, first-party CSS | GHSA-6g55-p6wh-862q (high), GHSA-r28c-9q8g-f849 (high), GHSA-qx2v-qp2m-jg93, GHSA-fxqj-rqcc-2cmp | **Open.** Verified: 15.5.27 is the newest 15.x and pins 8.4.31; next 16.3.8 pins 8.5.23 (past every range) — needs the Next 16 major. The app's own postcss is 8.5.28 |

**Combined result** with #28 + #29 + #30 merged: `npm audit` reports only
`next` (moderate aggregate) and its bundled `postcss` (high). No blind
override, forced audit fix or unrelated major upgrade was used.

**Not cleared by `npm audit`:** the Next.js September 2026 security release
(blog post 2026-09-30, fixes shipped in 15.5.27) postponed one critical and
one high fix "pending upstream coordination" with no public detail. They
are tracked as open; watch the Next.js security blog. None of the seven
shipped issues applies here (no `images.remotePatterns`, `next/image`,
`use cache`, Draft Mode, Pages Router or root catch-all).

### N-5 flakes

Five of six (global-rules.spec.ts) shared one cause: rule dialogs closed
before the list refetch landed (`invalidateRule` did not return its
promise). Fixed in #31 with a deterministic regression test that fails
without the fix. The remaining two — global-rules-impact.spec.ts:300 (FK
`validation_error` on create right after registration) and
draft-editor-real-conflict.spec.ts:211 (editor not visible in 30 s) — did
not reproduce locally (5×, retries off) and are left open rather than
masked.

### L-1 diagnosis (names and counts only; no value read)

- Supported name: `SECURITY_ENCRYPTION_KEYS` (comma-separated Fernet keys,
  newest first; `SecuritySettings.encryption_keys`).
- Loading: compose `env_file: [.env]` (repo root) into backend, worker and
  beat; pydantic also reads `.env` and `backend/.env`, but process
  environment wins.
- **The key is missing, not mis-routed.** It is empty or absent in the root
  `.env`, `backend/.env`, both examples, the Windows User, Machine and
  Process environment, and every DropPilot container.
- **No encrypted data exists locally:** `aliexpress_connections`,
  `shopify_connections` and `ebay_connections` hold 0 rows in both the
  `droppilot` and `droppilot_test` databases (Postgres volume created
  2026-09-17 08:57, after the `.env` was regenerated).
- Consequence: restoring the original key preserves nothing locally, but is
  still the only correct choice if that key is (or will be) used elsewhere.
  A brand-new key is safe **for this local database only** and must never
  be copied to a deployment that holds data encrypted with the old one.
  This is the owner's decision; no key was generated.

### CI evidence per PR (exact head, run)

| PR | Head | Run | Jobs | Backend pytest | Playwright |
|---|---|---|---|---|---|
| #26 | `8d7cd2e` | [36862031542](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/36862031542) | 10/10 | 3460 passed | 733 passed, 9 skipped, 0 flaky |
| #27 | `78c7f06` | [36855424195](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/36855424195) | 10/10 | 3370 passed | 716 passed, **6 flaky**, 9 skipped |
| #28 | `ce0e3e2` | [36861832686](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/36861832686) | 10/10 | 3370 passed | **722 passed, 9 skipped, 0 failed, 0 flaky** (full suite after the loading.tsx fix) |
| #29 | `02f7105` | [36877363551](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/36877363551) | 10/10 | 3370 passed | 721 passed, **1 flaky** (`global-rules-impact.spec.ts:635`), 9 skipped |
| #31 | `4ee7dde` | [36886676046](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/36886676046) | 10/10 | 3370 passed | 723 passed, 9 skipped, 0 flaky |
| #30 | `23daed7` | 36886931099 | in progress when this was written | — | — |

(3370 vs 3460: branches cut from `develop` lack this branch's 90 new tests.)
A green run on an earlier commit is not carried forward to a later one.
This ledger commit itself triggers a fresh run on #26.

### Integration rehearsal (local, not CI)

`develop` + #27 + #26 + #28 + #29 + #30 + #31 merged in order (throwaway
merge `71ae0ed`, never pushed):

- backend: ruff, format (468), mypy (236) clean; alembic head `0036`;
  pytest **3452 passed, 8 failed** — the same 8 environment-only failures
  (no `git` and a root `HOME` in the container) that pass on CI;
- Playwright full suite (CI-equivalent container, retries 2): **734 passed,
  9 skipped, 0 failed, 0 flaky**;
- `npm audit`: only the bundled `postcss` via `next` remains.

### Open after this pass

- `global-rules-impact.spec.ts` flakes (:300 on #27's run, :635 on #29's
  run) — not reproduced locally; different code path from #31's fix. Open.
- `draft-editor-real-conflict.spec.ts:211` — not reproduced. Open.
- Bundled `postcss` in `next` 15.5 — needs the Next 16 major. Open.
- Two postponed Next.js upstream fixes (1 critical, 1 high) — not yet
  published. Open, track.
- `SECURITY_ENCRYPTION_KEYS` — owner restores (L-1 above).

## Deviation from the requested setup

Two isolated worktrees were requested. This session's tooling only permits
writes inside its own worktree, so both branches were worked from that one
worktree in sequence (code branch first, then the plan branch). No other
checkout, including the developer's main checkout and its untracked
`AGENTS.md`, was modified.
