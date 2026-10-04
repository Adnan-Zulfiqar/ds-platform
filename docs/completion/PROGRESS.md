# Autonomous completion — progress

Current milestone: **AUT-02/AUT-03 closing; AUT-04 next** (2026-10-01).

## Baseline

| Item | Value |
|---|---|
| Repository | `https://github.com/Adnan-Zulfiqar/ds-platform` (remote `origin`) |
| Baseline `develop` | `72e76921fc80b203133423ad3bd92bd380c7e02b` |
| `main` | `3ce66d488e94ad3805fe24903deda99691c234a6` — untouched; deployment role unchanged |
| Deployment triggers | `.github/workflows/ci.yml` is the only workflow; push to `develop`/`main` runs CI only. No deploy job |
| Branch protection on `develop` | none (`404 Branch not protected`; rulesets `[]`) |
| Canonical toolchain | Node 22 (CI `NODE_VERSION`, `node:22-alpine`), Python 3.13, Postgres 17, Redis 7; npm per D-003 |

## AUT-01 — integration of the outstanding PRs

Each merge used `--match-head-commit` pinned to the head whose CI was read.
Status of each: `AUTHOR_VERIFIED — INTEGRATED — CURSOR_REVIEW_PENDING`.

| Order | PR | Head merged | PR CI run (that head) | Result | Merge commit |
|---|---|---|---|---|---|
| 1 | #27 CI lock install | `78c7f06` | 36855424195 | 10/10; Playwright 716 passed, **6 retry-passes** (N-5 baseline) | `b838fdb` |
| 2 | #26 Stage 5–9 remediation | `55dfff6` | 36891187844 | 10/10; pytest 3460; Playwright 733 passed, 9 skipped, 0 flaky | `2b71f65` |
| 3 | #28 Next 15.5.27 / React 19.0.8 | `e2095f5` | 36933835735 | 10/10; Playwright 722 passed, 9 skipped, 0 flaky | `249e18f` |
| 4 | #29 axios 1.20.0 | `02f7105` | 36877363551 | 10/10; Playwright 721 passed, **1 retry-pass** (`global-rules-impact.spec.ts:635`) | `f87a1f5` |
| 5 | #30 tiptap + dev transitives | `23daed7` | 36886931099 attempt 2 | 10/10; Playwright 722 passed, 9 skipped, 0 flaky. Attempt 1 hit the 35-min job timeout inside `npx playwright install` (no test ran) | `4182219` |
| 6 | #31 rule dialog refetch | `4ee7dde` | 36886676046 | 10/10; Playwright 723 passed, 9 skipped, 0 flaky | `df0e41f` |

Post-merge `develop` CI: run 36937771078 on `df0e41f` — **in progress when
written**; the five intermediate runs were cancelled by the workflow's
`cancel-in-progress` concurrency group (expected; they are not evidence).

Integrated lockfile (develop `df0e41f`): next 15.5.27, react 19.0.8, axios
1.20.0, @tiptap/core 3.31.4, js-yaml 4.3.2; 40 `libc` entries; `npm ci`
clean on `node:22-alpine` without rewriting the lockfile.

## AUT-02 — toolchain and security

| Item | Result |
|---|---|
| #28 lockfile `libc` loss | Fixed (`e2095f5`), merged |
| Backend dependency audit (first time in this programme) | 16 advisories (pyjwt 13, urllib3 3) → `fix/backend-pyjwt-urllib3` |
| Nested `.env` in Docker context (SEC-001) | PR #32 |
| Backend harness: 8 environment failures | Root causes confirmed: root `HOME` (backup-dir test), lost exec bits from a Windows copy (7 deployment-script tests). New harness uses `git archive` + non-root user + git repo. Full result pending |
| Remaining flakes (REM-N5-a/b) | Not started this pass |

## AUT-03 — local configuration

Local-only Fernet key created (D-004). backend, worker, beat: `SECURITY_ENCRYPTION_KEYS` SET; application round trip verified in backend and worker; API `/health` 200, nginx 200.

Provider verification levels (local stack):

| Provider | CONFIG_LOADED | OAUTH_READY | AUTHORISED | READ_VERIFIED | MUTATION_VERIFIED |
|---|---|---|---|---|---|
| Shopify | app keys SET (name-only check, 2026-10-01); encryption now SET | code + tests on develop | NO (0 connections) | NO | NO |
| AliExpress | app keys SET; encryption SET | code + tests on develop | NO (0 connections) | NO | NO |
| eBay | app keys SET; encryption SET | code + tests (EBAY-C1) | NO (0 connections) | NO | NO |

AUTHORISED and beyond need a human OAuth consent on a designated test
store/account (B-003).

## Next

1. AUT-04: reconcile PR #25 with the merged contracts; update it on develop.
2. AUT-05: Stage 10 implementation, ST10-A first.

## Update 2026-10-02

Merged into `develop` (each at the CI-verified head, `--match-head-commit`):

| PR | What | Head | PR CI | Merge |
|---|---|---|---|---|
| #33 | Completion tracking docs | `48d512d` | 10/10 | `b5ec686` |
| #34 | pyjwt 2.15.1, urllib3 2.8.0 (SEC-002) | `7fe78a9` | 10/10; pytest 3460; Playwright 734, 0 flaky | `28a2c59` |
| #25 | Stage 10 plan, reconciled (§0a) | `fdbda28` | 10/10; Playwright 725, **10 retry-passes** on unchanged code (global-rules, global-rules-impact, shopify-webhook-recovery) | `81f952f` |
| #35 | Commit before the response is sent (N-5 root cause) | `4d6c254` | 10/10; pytest 3462; Playwright 734, 0 flaky | `c88a04b` |
| #32 | Nested `.env` out of Docker contexts (SEC-001) | `767de14` | 10/10; pytest 3470; Playwright 734, 0 flaky | `7fe0a89` |

Post-merge develop CI for the integrated #27–#31 tree (`df0e41f`, run
36937771078): 10/10, pytest 3460, Playwright 734 passed / 9 skipped / 0 flaky.

Open PRs: #36 (Stage 10), #37 (DE-7), #38 (DE-6b) — author-verified locally,
CI pending at the time of writing.

**Harness.** Backend gates now run from `git archive <ref>` as a non-root
user with a git repository and disposable Postgres 17 / Redis 7; the eight
"environment-only" failures from earlier passes are gone (3460–3462 passed,
0 failed). Lesson recorded: a frontend-only change still needs the backend
unit suite — `test_stage_7_contract.py` reads `frontend/types/api.ts`, and
#36's first CI run failed on it.

**Flake evidence.** The 10 retry-passes on #25's docs-only run were mostly
the commit-ordering symptoms (FK error right after register; signed-in page
missing; a rule created through the API not yet listed). #35 merged after
its own CI showed 0 flaky; whether the flakes are gone is judged on the
next runs, not on one.

## AUT-09 evidence — backup/restore drill on disposable databases (2026-10-02)

Procedure: `docs/operations/BACKUP_RUNBOOK.md` §7, at `develop` `7fe0a89`
(git archive of the commit; Postgres 17 container `dp-drill-pg`; tools run
as a non-root user; a throwaway backup key generated inside the container,
never printed, discarded with it).

| Step | Result |
|---|---|
| Migrate source to head | `0036` |
| Seed through the real API + DB seed helper | 2 tenants, 10 products (5 drafts each, 1 published), a pricing rule each; 42 tables, 12 non-empty |
| Encrypted backup (`create_database_backup.py`) | rc 0 |
| Verify (`verify_database_backup.py --all`) | rc 0 |
| Restore into a new empty database (`--apply`) | rc 0; Alembic `0036`; 42 tables |
| Compare per-table count + md5 of ordered rows | **identical, 42 / 42** |
| `tenant_id` nullable anywhere after restore | 0 columns |
| Corrupted copy / wrong key | verify rc 5 / rc 5 (refused) |
| Wrong source identity / non-empty target | restore rc 2 / rc 2 (refused) |

Not covered by this drill: encrypted credential columns (no provider
connection existed in the seed), a production backup, off-site copy, timed
restore — all remain the external items in the runbook §8.

## Candidate — 2026-10-02

Further merges (each at its CI-verified head): #37 (`374bdd1`), #38
(`1d6e34d`), #39 (`25f0429`), #36 Stage 10 (`fbadddb`), #40 Next 16
(`25134a5`).

**Candidate `develop` @ `25134a5`, post-merge CI run 36954786975: 10/10;
pytest 3475 passed; Playwright 767 passed, 8 skipped, 0 failed, 0 flaky.**

## Second candidate — 2026-10-02

| Step | Result |
|---|---|
| PR #42 (lint 25 → 0, rules restored; `proxy.ts`) | head `e1667df`, PR CI 36993008546 10/10 (pytest 3475; Playwright 769, 0 flaky); merged `9a62b9e` |
| **Candidate `develop` @ `9a62b9e818350fa8dafc16276a3ea0200411971b`** | **post-merge CI 36995948409: 10/10; pytest 3475 passed; Playwright 769 passed, 8 skipped, 0 failed, 0 flaky** |
| Local full Playwright before merge (retries 0) | 769 passed, 8 skipped, 0 failed |
| Flake repeats (retries 0, ×5) | real-conflict 70/70; Studio bulk 35/35 — evidence only (DP-CR-015) |
| Backup/restore drill + synthetic encrypted credential | at `48008107…` (backend tree identical to the candidate): 42/42 digests identical; credential MATCH after restore, ciphertext at rest; different key refused (DP-CR-007) |
| Local candidate stack | `dp-candidate`, http://localhost:18080; images from `git archive 9a62b9e`, no `.env` in any image; browser walk-through passed (`docs/operations/LOCAL_CANDIDATE_STACK.md`) |
| Docker secret images | assessed; task-owned `dp-remed-*` removed; clean `dp-e2e-clean` harness image (DP-CR-024) |
| Docker Desktop outage | after the 05:14Z system sign-out/shutdown; fixed by renaming two stale-socket directories; owner stack and volumes intact (B-008). The owner's Open-Higgsfield dev server on :3001 was also down after that sign-out and was left as found |

## Independent review — 2026-10-02

| Step | Result |
|---|---|
| Cursor review of `develop` @ `e63508e` | Implementation **ACCEPTED WITH NON-BLOCKING NOTES**; release **NOT READY** (`docs/reviews/cursor/INDEPENDENT_REVIEW_e63508e.md`) |
| Backup/restore drill on `e63508e` (author, after the verdict) | 42/42 digests identical; credential MATCH, ciphertext at rest; negatives refused |
| Eight Playwright skips | Titles recovered from push CI 37003066025; all are worker-absent or live-AliExpress guards |
| Tag / flakes / Stage 8 | Untagged; flakes open; Stage 8 not complete — as Cursor directs |

## Remaining roadmap, Track A — 2026-10-03

Owner standing order: execute `docs/CLAUDE_REMAINING_ROADMAP.md` Tracks A→D.
Pinned baseline `develop` @ `7af51f9` (merge of PR #45).

| Item | Result | Kind |
|---|---|---|
| A1 — clean owner images (B-007) | `droppilot-backend/worker/beat/frontend` rebuilt from `git archive 7af51f9`; `/app/.env` absent in all four (boolean check, nothing printed). Running containers not recreated — owner step | Local runtime |
| A2 — flakes (DP-CR-015) | A: pre-#35 ordering + 0.4 s commit delay → 3/3 runs fail with the original symptom; #35 ordering + same delay → symptom 0/14. Closed by author, independent confirmation pending. New lead O-1 recorded. B: OPEN, cause unproven | Local runtime, disposable |
| A3 — live AI (B-002) | OPEN — owner checklist in `BLOCKERS.md`. Provider code is Track B | — |
| A4 — Shopify test store (B-003) | OPEN — owner checklist | — |
| A5 — AliExpress → Shopify (B-004) | OPEN — owner checklist. Draft Editor Stage 8 stays incomplete | — |
| A6 — `phase-9-complete` | Not created: A3–A5 evidence is missing and no "tag phase-9 now" was given | — |
| A7 — production deploy | Not in autonomous scope | — |
| Found: root `.env` lost `SECURITY_ENCRYPTION_KEYS` (B-009) | No ciphertext in `droppilot` (0 rows in every encrypted column); owner decides the key | Local check, counts only |

## Remaining roadmap, Track C audit — 2026-10-03

| ID | Finding |
|---|---|
| C1 — DE-6b / DE-7 | Merged (#38, #37) and covered by Cursor's acceptance of `e63508e` (IR-02/IR-06 context). Re-read against `docs/DRAFT_PRODUCT_EDITOR_PLAN.md`: nothing in those stages is unimplemented. No rewrite |
| C2 — DE-8a bulk tools | Resolved as D-010: AI Studio bulk + Global Rules bulk pricing; "Refresh ×n", "Publish Selected", scheduling are future. Nothing implied by the plans is missing |
| C3 — DE-8b live E2E | Blocked on A4/A5 (B-003, B-004) |

## Remaining roadmap, Tracks B and D — 2026-10-03

| Item | Result |
|---|---|
| Track A docs | PR #47, CI 10/10 on `f92346f`, merged `8ee9eba` |
| Track B — `OpenAIProvider` | PR #46: local gate pytest 3497 passed, CI 10/10 on `12c7650`, merged `9ffa700`. Mocked transport only; no real OpenAI call (B-002 open) |
| Track D — EBAY-C2 listing setup | PR #48: local gate pytest 3498 passed, alembic `0037`; Playwright 44/44 (panel, channels, eBay specs). Mocked eBay transport only |
| Track D — EBAY-C3…C6 | Not started. `docs/ebay/EBAY_C3_PROPOSAL.md` lists the architecture decisions (multi-channel readiness, eBay store model, item specifics) that CLAUDE.md §12.1 requires the owner to approve first; C4–C6 build on C3 |

## Track D, EBAY-C3 to C6 — 2026-10-03

Owner approved `EBAY_C3_PROPOSAL.md` (D-C3-1 to D-C3-4) and ordered C3 → C6.

| Milestone | PR / commit | Local verification | Kind |
|---|---|---|---|
| C3 publish | PR #49 | Full gate on first head: 10 failed + 1 error, 3533 passed — test-harness faults (form body parsed as JSON; missing per-test Redis fixture; stale unit test), fixed; affected files 47 passed. Playwright 70/70 | Mocked eBay transport |
| C4 price/stock | stacked on C3 | Integration tests (mocked); a catalogue Playwright failure (duplicate listings request from the new panel) found and fixed | Mocked eBay transport |
| C5 orders | stacked on C4 | Integration tests (mocked); Playwright eBay orders spec | Mocked eBay transport |
| C6 operations | stacked on C5 | Unit + integration (health view) | Mocked eBay transport |

No real eBay call has been made by any of these. Owner steps: `docs/ebay/EBAY_C6_OPERATIONS.md`.

## Review of the new code, owner stack, flake B — 2026-10-03

| Item | Result |
|---|---|
| Owner DB backup before touching the stack | `pg_dump -Fc` of `droppilot` (41 table-data entries, readable by `pg_restore -l`), kept in the task scratchpad |
| Owner stack | backend/worker/beat/frontend recreated on images from `develop` `02e12fc` (no `.env` inside). The compose file bind-mounts `./backend` onto `/app`, so the running code is the owner's checkout (`0016543` plus the owner's uncommitted changes); the database was migrated 0034 → 0037 to match that checkout and **not** further, so its alembic stays consistent. `/health` 200. AI: `OpenAIProvider` loaded |
| Reviews | Three independent reviews (publish path; sync/orders/deletion; OpenAI + eBay UI): 11 confirmed findings, all fixed in this change; OpenAI provider and settings precedence: nothing confirmed |
| Flake B | Mechanism reproduced: guard reverted 5/5 fail, guard present 5/5 pass (DP-CR-015) |

## Track E — 2026-10-03

Owner order D-012: E1 → E7, gates → PR → CI → merge for each item.

- **Merged:**
  - B-011 (#54);
  - E1 Shopify fulfilment (#55);
  - E2 sale fees (#56);
  - E3 notification email (#57);
  - E4 team invitations (#59);
  - Track E proposals and blockers (#58);
  - E7 WooCommerce W1–W5 and W4b webhooks (#60–#65).
- **Blocked on the owner:**
  - B-013 platform admin;
  - B-014 billing;
  - B-015 Etsy/TikTok registrations;
  - B-016 WooCommerce sweep approval.
- **Defects found by the gates and fixed before merge:**
  - E3: email preferences were missing from the GDPR erasure declaration
    (the schema-wide guard test caught it).
  - E3: a second sweep in one session resent emails (autoflush is off).
  - E4: a failed invitation email left the row behind. The email is now
    sent before the row is written.
  - W1: workspace closure did not clear `stores.encrypted_credentials`.
    This was a gap from before Track E.
- **Test-only fixes:**
  - W2: a duplicated store id in a test.
  - W4a: comparing a Decimal amount as a string.
- **Operational:** `python -` in this shell starts a spinning REPL. It
  happened twice and was stopped both times. Use script files instead.
- **Not verified live:**
  - a WooCommerce store;
  - Resend delivery;
  - Shopify reconnect with the new fulfilment scopes;
  - webhook delivery to a public deployment.

## E5, E6, CSP fix and application analysis — 2026-10-04

- **Merged into `develop`** (each on 10/10 CI, merged with
  `--match-head-commit`): E5a–E5d platform operator console (#69–#72),
  E6a–E6c Stripe billing (#73–#75), CSP supplier-image fix (#76).
- **Tunnel:** the owner moved `api.whiteto.com` off the DESKTOP-8D5VPLK
  connector. 10/10 probes now reach the backend on this PC (previously
  0/10). The owner then completed AliExpress OAuth and imported a listing.
- **Defect found live by the owner:** every imported product image was
  blocked by the CSP `img-src` (only Google avatars were allowed). Fixed in
  #76 with named hosts, not `https:`; regression test in `csp.spec.ts`.
  The owner's frontend container was rebuilt from the merged tree and
  recreated; the live header was verified on `localhost:3000`.
- **Defect found by the E6b tests before merge:** `require_billing_write`
  ran before the tenant was bound (500 instead of 402). Fixed in #74.
- **Application analysis** (three read-only sweeps, key claims re-checked
  by hand):
  - `shipment.refresh` Celery task fails every run with
    `TypeError: got multiple values for argument 'limit'`
    (`tasks/shipments.py:20`; reproduced locally, scheduled every 6 h).
  - Scheduled stock sync pushes only to eBay, not WooCommerce
    (`tasks/inventory.py:17`).
  - User-menu Profile link → `/settings/profile`, which does not exist.
  - Shopify `publish_product` / `push_inventory` / `push_price` tasks are
    defined but never enqueued; no automatic Shopify price/stock push.
  - No Shopify GDPR webhooks (`customers/redact`, `shop/redact`).
  - Backend has edit/delete for automation rules, pricing rules and stores,
    and a prompts API; none has a UI.
  - No auto-ordering to the supplier and no supplier-tracking sync.
  - No production deployment exists; no Sentry/metrics/alerting.
  None of these were fixed this session; the owner has not yet chosen an
  order. B-015 and B-016 are deliberately untouched (owner instruction).
- **Harness notes:** retargeting a PR's base does not start CI
  (`pull_request` has no `edited` type); push a develop merge instead.
  Two gate runs at once collide on the shared `dp-gate-*` containers.
- **Not verified live:** a Stripe checkout; the billing gate through each
  marketplace's publish endpoint; the trial-claim hooks against real
  stores; the E5 console against a real operator account.
