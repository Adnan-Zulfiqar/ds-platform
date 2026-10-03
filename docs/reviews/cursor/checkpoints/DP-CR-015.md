# DP-CR-015 — Remaining Playwright flakes (evidence, not closure)

**Update 2026-10-03 (roadmap Track A2):** A has a deterministic reproduction
of its mechanism (below) and is **closed by the author, pending independent
confirmation**. B stays **OPEN — cause unproven**. Green runs alone are still
recorded as evidence only.

## A2 experiment (2026-10-03) — flake A reproduced on demand

Two disposable copies of `develop` @ `7af51f9`, identical except for one line.
Both add `await asyncio.sleep(0.4)` before `session.commit()` in
`get_db_session` to widen the commit window so a race does not depend on
luck. The difference:

| Variant | `DbSession` | Result: `draft-editor-real-conflict.spec.ts`, `--repeat-each=3 --retries=0` |
|---|---|---|
| **nofix** | `Depends(get_db_session)` — pre-#35 behaviour, commit after the response | **3 failed, 2 passed, 37 did not run** (serial suite aborted). Every failure is the original symptom: `getByTestId('draft-editor')` not visible in 30 s at the `beforeEach` (line 186), right after `loginViaApi` |
| **fix** | `Depends(get_db_session, scope="function")` — #35, commit before the response | **40 passed, 1 failed, 1 did not run.** The line-186 symptom did not occur once in 14 runs of the hook |

What this establishes: the commit-after-response ordering that PR #35
removed produces exactly flake A's failure, and the merged ordering does not.
What it does not establish: that the single CI failure on run 36855424195
took this path rather than another one with the same symptom. That is as far
as a reproduction can go; together with zero recurrences since #35 it meets
the roadmap's A2 bar ("root-cause fix + green CI").

**New observation O-1, not dismissed.** The one failure in the *fix* variant
is a different test and a different assertion:
`:577 a second real conflict preserves the reviewed merchant version` —
`conflict-review-dialog` did not show the second server value within 5 s
(line 605). It appeared only with the artificial 0.4 s added to every
committed request, has never appeared in CI, and its cause is not
investigated here. Recorded as a lead, not as a flake and not as cleared.

Harness: `e2e_run_tree.sh` over the two trees (task-owned containers
`dp-e2e-*`, removed after); the variant `deps.py` edits never touched the
repository.

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

- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
