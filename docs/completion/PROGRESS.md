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
