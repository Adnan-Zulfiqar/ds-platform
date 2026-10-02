# DP-CR-023 — Draft Editor Stage 8 "bulk tools" traced to its sources (B-006)

- **Question:** is "Refresh ×n" (and any other bulk action) a release requirement?
- **Authoritative sources:** `docs/DRAFT_PRODUCT_EDITOR_PLAN.md` Stage 8 "Bulk tools + live E2E verification — Pending" and "Remaining: … bulk tools …"; `docs/PRODUCT_WORKSPACE_V2_PLAN.md` §4 Stage 8 "Bulk tools, scheduling, polish". Neither defines a bulk action.
- **Where the specific actions appear:** "Optimize ×n / Refresh ×n" only in the UX design baseline's proposal (`docs/ux/ux-l2d-01-baseline.md` §183), which the same programme then recorded as "Not built: bulk selection (no bulk endpoint)" (UX-L2D-04 row). "Publish Selected" is only a terminology label (`PRODUCT_WORKSPACE_V2_PLAN.md` §7), and the baseline states "Bulk publish is **not** offered".
- **Disposition:** "Refresh ×n", "Publish Selected" and "scheduling" are **future scope** (unrequested proposals / undefined). Stage 8's bulk requirement is met by the bulk capabilities that exist: AI Studio bulk preview runs (Stage 10, PR #36) and Global Rules bulk pricing on drafts. Stage 8's other half — live AliExpress → Shopify E2E (DE-8b) — remains **externally blocked** (B-003, B-004); it is not dropped.
- **No code change.**
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
