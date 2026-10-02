# DP-CR-019 — Next 16 lint findings fixed; middleware.ts → proxy.ts (PR #42)

- **Original IDs:** D-009 (follow-up of PR #40), Next 16 `middleware` deprecation.
- **Base / head:** develop `48008107ba4f3a09363498ac89e3b91e82dc9c07` → `e1667df` (commits `6dba321` proxy, `e1667df` lint); merge `9a62b9e818350fa8dafc16276a3ea0200411971b`.
- **Count reconciled:** 25 warnings = 23 from `react-hooks/refs`, `set-state-in-effect`, `purity` (downgraded in #40) + 2 default-warn (`incompatible-library` on `form.watch`, `no-location-assign-relative-destination` in `app/error.tsx`).
- **Change:** all 25 fixed; the three rules restored to their defaults (error); no suppression comment. Patterns per site are listed in the commit message (useSyncExternalStore for external state incl. new `useHydrated()`; adjust-during-render instead of prop→state effects; derived defaults; no ref access during render; clock read in the filter event; `useWatch`; `Link`).
- **Behaviour changes to inspect:** global-rules page now wraps the workspace in `Suspense` (needed for `useSearchParams`); the error page's "Go home" is client navigation (the segment unmounts, clearing the boundary) instead of a full reload; the import dialog's ship-to follows the recommended country until the merchant picks one (before: copied once when empty); the orders date window is fixed when the filter is chosen.
- **Proxy:** per nextjs.org proxy.js docs (updated 2026-09-07): rename + `export function proxy`. Node.js runtime; no `runtime` config present; nonce via Web Crypto. Build log shows no deprecation (`ƒ Proxy (Middleware)` in the route table).
- **Tests:** two new proxy contract tests in `csp.spec.ts` (protected pages no-store; client `x-nonce` replaced). One assertion I first wrote ("public pages are not no-store") was wrong — Next marks every dynamic page no-store — and was removed before merge.
- **Evidence:** local full Playwright, retries 0: 769 passed, 8 skipped, 0 failed; PR CI 36993008546 10/10 at `e1667df` (pytest 3475; Playwright 769, 0 flaky).
- **Author verification:** PASS.
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
