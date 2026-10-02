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
| Backup/restore drill on `e63508e` (author, after the verdict) | 42/42 digests identical; credential MATCH, ciphertext at rest; negatives refused (DP-CR-025) |
| Eight Playwright skips | Titles recovered from push CI 37003066025; all are worker-absent or live-AliExpress guards (DP-CR-025) |
| Tag / flakes / Stage 8 | Untagged; flakes open; Stage 8 not complete — as Cursor directs |
