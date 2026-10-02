# DP-CR-021 — Draft Editor full readiness (DE-6b, PR #38)

- **Requirement:** `SCOPE_MATRIX.md` DE-6b — readiness shows every server-enforced publish blocker; the source plan says only "full readiness".
- **Base / head:** develop `81f952f` → `d8b356d`; PR [#38](https://github.com/Adnan-Zulfiqar/ds-platform/pull/38).
- **Gap report (read-only exploration of develop):** the "Before you publish" sidebar rendered only client hints; with a server blocker present it still said "No title, description or image suggestions". "Open Integrations" (store_disconnected, section `publishing`) was routed by section back to the open tab; "Reload draft" only switched tabs. The client image count included soft-deleted images.
- **Backend defect found while confirming the last point:** after removing an image, the removal response and `GET /drafts/{id}` still listed it (both images at position 0). `Product.images` loads soft-deleted rows; the detail mappers did not filter. Fixed with `Product.live_images`; the Shopify payload, readiness and SEO score already filtered.
- **Change:** sidebar lists server blockers first ("Blocks publishing"), client hints as "Suggestions"; readiness runs on every tab once a store is chosen and the draft is saved; action string checked before section.
- **Tests:** `test_draft_image_removal.py` (2; the removal case failed before). Three Playwright cases fail on develop's components, pass here. Updated four assertions that encoded the old "no suggestions under a blocker" wording.
- **Results:** backend 3462 passed (full), ruff/format/mypy clean; editor specs 103 passed, retries 0.
- **Not changed:** `unsupported_channel`, `store_required` are unreachable from the editor (fixed channel; store chosen first). Client hints remain (labelled suggestions), not merged into one model.
- **Author verification:** PASS (local); PR CI pending.
- **Independent review:** PENDING — NOT YET PERFORMED
