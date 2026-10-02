# DP-CR-001 — Stage 5–9 review remediation (PR #26)

- **Original IDs:** A-1, A-2, B-1, B-2, B-3, C-1, D-1, D-2, E-1, G-1, G-2, G-3, H-1…H-5, I-1, I-2, J-1, J-3, J-4, K-1…K-5, L-1, N-1, N-2, N-4 and the INFO rows.
- **Requirement and acceptance criteria:** each finding's fix and test as listed in `docs/REVIEW_REMEDIATION_STAGE_5_9.md`.
- **Base / implementation head:** `72e76921fc80b203133423ad3bd92bd380c7e02b` → `55dfff6692ce0d2c2273d17fb45a876601d5eaa8`.
- **PR / integration commit:** [#26](https://github.com/Adnan-Zulfiqar/ds-platform/pull/26), merge `2b71f65` (after #27, `b838fdb`).
- **Files changed, before/after, design choices:** the ledger, one row per finding.
- **Tenant/auth/security:** composite tenant FKs (migrations 0035, 0036); single-use OAuth state through MULTI/EXEC `take_once` (Redis 3.0.504 baseline, no GETDEL); SSRF fetch residuals (B-3).
- **Concurrency/retry/side effects:** `FOR NO KEY UPDATE` run lock; NOWAIT cancel with a cancel-request table; 1500/1680 s task limits with continuation; per-item failure bound; E-1 keeps published AI text on an ordinary publish.
- **API/schema/migration:** `0035`, `0036` with working `downgrade()`; `ProductVersionRead.isPipelineCandidate`; tone allowlist (G-3 — an intentional narrowing of accepted input).
- **Tests:** per finding in the ledger, several with failure-before evidence.
- **Commands and results:** PR CI 36891187844 at `55dfff6`: 10/10, pytest 3460 passed, Playwright 733 passed / 9 skipped / 0 flaky. Code head `8d7cd2e`: run 36862031542, same counts.
- **Known limitations:** two N-5 flakes open (DP-CR-015); live provider verification external.
- **Recovery:** revert the merge commit; both migrations downgrade.
- **Cursor must inspect:** E-1 content resolution across editor, API, retry and Celery paths; 0035/0036 on a populated schema; bulk lease/cancel races.
- **Author verification:** PASS (CI on the exact head).
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
