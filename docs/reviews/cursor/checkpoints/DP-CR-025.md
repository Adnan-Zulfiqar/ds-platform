# DP-CR-025 — Author follow-ups to the independent review of `e63508e`

Evidence the author gathered after Cursor's verdict, for the gaps the report
names. Author evidence only; Cursor has not reviewed it. None of it changes
release readiness (still **NOT READY**) and none of it is provider-verified.

## Backup/restore drill on `e63508e` (IR-07)

- **Target:** `git archive e63508e014a89e611d535476904d7d1888554665`, run in
  a disposable `python:3.13-slim` container against a disposable
  `postgres:17-alpine` (`dp-drill-pg`, network `dp-drill-net`), both removed
  afterwards. The owner's databases and the `dp-candidate` stack were not
  touched.
- **Result:** migrate to `0036`; two tenants seeded through the real API;
  encrypted backup created and verified; restored into a new empty database;
  per-table digests **IDENTICAL (42 tables)**; no nullable `tenant_id`;
  synthetic encrypted credential **MATCH** after restore, ciphertext at rest;
  decrypt with a different key refused (rc=1). Negatives: corrupted copy
  (rc=5), wrong key (rc=5), wrong source identity (rc=2), non-empty target
  (rc=2) — all refused.
- **Scope:** a disposable local drill. It is not a production backup,
  off-site copy or key-custody test (runbook §8).

## The eight Playwright skips (Coverage table, "not re-listed")

From the `Run Playwright (chromium)` step of push CI run 37003066025 on
`e63508e` (769 passed, 8 skipped). The list reporter prints titles, not skip
reasons; the reason column is read from each test's skip guard and the CI
environment, not from the log.

| # | Test | Skip guard that applies in CI |
|---|---|---|
| 309 | `global-rules-impact.spec.ts:351` Asynchronous application › the worker finishes the run and the results persist | No Celery worker consumes in the e2e job |
| 310 | `global-rules-impact.spec.ts:388` Asynchronous application › results can be filtered by outcome | same |
| 312 | `global-rules-impact.spec.ts:443` Cancellation › a completed run offers no cancel action | same |
| 399 | `integrations.spec.ts:120` Integrations page › starting connect moves the connection to pending | AliExpress platform credentials are blank in CI (422 "not configured") |
| 400 | `integrations.spec.ts:131` Integrations page › disconnecting returns the card to not connected | same |
| 492 | `products.spec.ts:469` Drafts import flow › shows AliExpress as connected after OAuth completes | AliExpress OAuth cannot complete without a live gateway |
| 493 | `products.spec.ts:491` Drafts import flow › displays an imported draft after API seeding | depends on that connection |
| 494 | `products.spec.ts:509` Drafts import flow › imports through the dialog when AliExpress is connected | same |

For the first three, broker delivery is exercised by the separate
"Celery — broker verification" CI job (part of the 10/10), but the
browser-level asynchronous rules journey itself stays unexercised in CI. The other five are the live
AliExpress journey that B-004 already blocks; they are not a gap that
repository work can close.

## Not done

- Full Playwright re-run on `e63508e` by an independent reviewer.
- Flakes: still open (IR-05). No new repeats were run.
- `phase-9-complete`: untagged (D-008, Cursor remediation item 1).
- Claude return review checkpoint (end of Cursor's report): open.

- **Independent review:** not performed for this checkpoint.
