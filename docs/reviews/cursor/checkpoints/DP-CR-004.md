# DP-CR-004 — axios 1.20.0 (PR #29)

- **Base / head:** `72e7692` → `02f7105dc3ae6918570675f4ca0389a7bf98b410`; merge `f87a1f5`.
- **Change:** axios ^1.20.0 (12 advisories, 7 high); 5-line lockfile diff (npm 11).
- **Behaviour the completion roadmap asks to verify (§7.2):** request handling, auth expiry/refresh, error mapping, cancellation, uploads. Covered only by the existing Playwright suite; no axios-specific regression test was added.
- **Results:** PR CI 36877363551 10/10; Playwright 721 passed, 1 retry-pass `global-rules-impact.spec.ts:635` (N-5, unrelated path).
- **Recovery:** revert the merge.
- **Cursor must inspect:** the API client interceptors against the axios 1.20 changelog.
- **Author verification:** PASS (CI).
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
