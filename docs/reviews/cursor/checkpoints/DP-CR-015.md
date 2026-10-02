# DP-CR-015 — Remaining Playwright flakes (evidence, not closure)

Both items stay **OPEN**. Green runs are recorded as evidence only.

## A. `draft-editor-real-conflict.spec.ts:211` (editor not visible in 30 s)

- **History:** one failure on run 36855424195 (develop + #27, before #35).
- **Probable cause (inferred, not reproduced):** the hook logs in through the API, then navigates; the app's first `/auth/refresh` looks up the refresh-token row the login wrote. Before PR #35 the commit happened after the 2xx, so the row could be missing → `refresh_token_not_recognised` → 401 → no session → no editor. PR #35's unit tests prove the ordering defect in general; they do not reproduce this spec.
- **Evidence since #35:** CI runs on every head merged after `c88a04b` show 0 flaky (#38, #37, #36, #40, #42, candidate runs); local, retries 0, `--repeat-each=5`: 70/70 (2026-10-02, tree of `e1667df`, the head merged as `9a62b9e`).

## B. AI Studio "the 51st product cannot be selected" (one retry-pass on PR #36 run 36945059758)

- **Found and fixed (cbfa72b):** Select page acted on placeholder rows while a page loaded; now disabled until the requested page is shown; a regression test (list delayed 800 ms) fails without the fix.
- **Not proven:** that this was the CI flake's path — on a view switch the Published first page is normally already cached.
- **Evidence since:** local, retries 0, repeat 5: 35/35 (before merge) and 35/35 (2026-10-02 pass); CI on #36's final head and later heads: 0 flaky.

## What would close them

A deterministic reproduction of the original failure, or a sustained run of CI history; neither exists yet.

- **Independent review:** PENDING — NOT YET PERFORMED
