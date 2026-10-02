# DP-CR-022 — Draft Editor idempotent publish UX (DE-7, PR #37)

- **Requirement:** `SCOPE_MATRIX.md` DE-7 — repeated clicks/retries never duplicate a listing; status is clear.
- **Already in place (verified by reading, not changed):** backend row lock + re-check + listing re-read, update-or-adopt instead of create (`sync.py`), with concurrency and lost-response integration tests; frontend in-flight ref and disabled button; busy 409 copy.
- **Defects fixed:** every 409 except `shopify_publish_busy` opened the edit-conflict review, including `published_ai_content_unavailable`; a request with no reply was reported as "Publish to Store failed" although it may have reached Shopify.
- **Base / head:** develop `81f952f` → `76636c2`; PR [#37](https://github.com/Adnan-Zulfiqar/ds-platform/pull/37).
- **Tests:** two Playwright cases fail on develop's editor and pass here; `review-remediation` + `publish-integrity` 28 passed, retries 0.
- **Not addressed:** the response's `updated` flag is always true (no created/updated distinction); the double-click test clicks a disabled button with `force: true`, so it does not isolate the ref guard; real-Shopify retry behaviour is external (B-003).
- **Author verification:** PASS (local); PR CI pending.
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
