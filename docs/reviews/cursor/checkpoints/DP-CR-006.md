# DP-CR-006 — Global-rules dialog closes only after the list shows the rule (PR #31)

- **Original IDs:** N-5 (five of the six retry-passes on run 36855424195).
- **Base / head:** `72e7692` → `4ee7dde66aaae25998f8db73d20c5963c5cd9a77`; merge `df0e41f`.
- **Root cause:** `invalidateRule()` did not return the invalidation promise, so the mutation's `onSuccess` resolved and the dialog closed before the list refetched. On a slow link a merchant briefly did not see the rule they had just saved.
- **Test:** "the dialog closes only once the saved rule is in the list" delays the list GET by 3 s; fails without the fix, passes with it. 225 runs (`--repeat-each=3`, retries off) green after the fix.
- **Results:** PR CI 36886676046 10/10; Playwright 723 passed, 0 flaky.
- **Not closed here:** `global-rules-impact.spec.ts:300` / `:635`, `draft-editor-real-conflict.spec.ts:211` (DP-CR-015).
- **Author verification:** PASS.
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
