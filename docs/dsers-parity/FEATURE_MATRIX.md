# DSers-Parity Feature Matrix

Audited against the running repository, not against intent. Every row cites
the file(s) that are the evidence. Status values:

- **Existing and verified** — code exists and has passing automated test
  coverage exercising the behaviour described.
- **Existing but incomplete** — code exists and works for the common case,
  but a specific documented gap remains.
- **Missing** — no implementation.
- **Blocked by external API/permission** — cannot be built with the access
  this platform currently has to the supplier/channel API, documented at the
  cited source.
- **Planned milestone** — scoped in [MASTER_ROADMAP.md](MASTER_ROADMAP.md),
  not started.

Audit date: 2026-08-12 (M1); §3 corrected 2026-08-12 (M2A); §3 rebaselined
2026-08-13 (M2A acceptance-fix pass, capability-by-capability). Branch:
`feature/dsers-parity-m2a-editor-foundation`.

---

## 1. Product sourcing & discovery

| Capability | Status | Evidence |
|---|---|---|
| Import a single product by ID/URL | Existing and verified | `backend/app/services/product_import.py::import_product`, `backend/tests/integration/test_products.py::TestImport` |
| Browse the AliExpress recommendation feed (no import) | Existing and verified | `ProductImportService.browse_feed`, `backend/app/api/v1/products/router.py` (`/feeds/{feed_name}`), `TestFeedBrowsing` |
| Keyword/category search for sourcing | Blocked by external API/permission | `frontend/components/products/import-product-dialog.tsx` docstring: *"Keyword search returns `NGSELECTION_SEARCH_ERROR` on this account — verified against the live gateway"* |
| Bulk import (CSV / multi-URL paste) | Missing | No batch endpoint; `POST /products/import` accepts one `externalId` per call |
| Winning-product / trend discovery tools | Missing | No such module exists |
| Multi-supplier sourcing (Temu, CJ, Zendrop, etc.) | Missing | Only `ProductSource.ALIEXPRESS` and `manual` exist (`backend/app/models/product.py`) |

## 2. Importing → editable draft (M1 scope)

| Capability | Status | Evidence |
|---|---|---|
| Tenant-scoped import request, normalized, deduplicated by natural key | Existing and verified | `uq_products_tenant_source_external` constraint; `ProductImportRepository.find_in_progress`; `TestImport::test_import_is_idempotent` |
| Draft lands in Drafts, never in Products, until publish | Existing and verified | `ProductRepository.list_drafts`/`list_published` split on synced `StoreListing`; `docs/PRODUCT_WORKSPACE_V2_PLAN.md` (Stage 0: Done); `TestTenantIsolation::test_both_tenants_may_import_the_same_supplier_product` |
| Failed import stays visible with error + retry action | Existing and verified (fixed 2026-08-12) | See [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md) §"The durability bug" — `ProductImportService._persist_failure_durably`, `backend/tests/integration/test_product_import_transaction_durability.py` |
| Retry a failed import without re-entering parameters | Existing and verified | `ProductImportService.retry_import`, `POST /api/v1/products/imports/{id}/retry`, `TestRetryImport` (5 tests) |
| Retry does not create a duplicate draft | Existing and verified | `TestRetryImport::test_retry_does_not_duplicate_an_existing_draft` |
| Duplicate-import warning in the UI before submit | Existing and verified (fixed 2026-08-12) | Server-authoritative `GET /api/v1/products/import/check`, tenant-scoped natural-key lookup, not a client cache scan — see [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md) §"Server-authoritative duplicate detection"; `TestDuplicateImportCheck` (7 tests) |
| Variant count on the Drafts list | Existing and verified (added 2026-08-12) | Correlated `COUNT` alongside the existing list query, `ProductRepository._variant_count_column` — see [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md) §"Variant-count column"; `TestVariantCount` (3 tests) |
| Import provenance / supplier snapshot preserved separately from merchant edits | Existing and verified | `Product.supplier_title`/`supplier_brand`/`supplier_description` vs. merchant-editable `title`/`brand`/`description`; `_SYNCED_FIELDS` divergence protection in `product_import.py` |

## 3. Premium product editor

**Correction (M2A, 2026-08-12):** the row below this note previously read
"Basic safe draft view (title, images, variants, cost, status)," which
significantly understated what is actually built. Verified directly against
`frontend/components/drafts/draft-product-editor.tsx` and the `editor-header/`
component tree, a full premium editor already exists on `develop` — a
three-layer sticky header (breadcrumb, identity, save-state, publish action,
supplier-sync status, actions menu), and tabs for Overview, Description,
Media, Variants, Pricing, Inventory, Shipping, SEO, with a Draft Preview
panel and AI version history. This was built across `cursor/draft-product-editor`,
`cursor/premium-product-editor`, and `cursor/product-workspace-v2`
(migrations `0013`–`0022`) — separately from, and largely before, the M1
dsers-parity work — and this matrix simply had not been re-audited against
it. See [M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md) for the full account and
what M2A actually delivered as a result (hardening, not a new editor).

**Rebaseline method (2026-08-13):** each row below was re-checked against
the running code and its test suite individually — not carried forward from
the previous table — using this document's own status taxonomy: *Implemented
and verified* (code exists, automated test coverage exercises it),
*Implemented but incomplete* (works for the common case, a specific gap is
documented), *Present but unverified* (code exists, no automated test
exercises it directly), *Missing*, *Deferred* (explicitly scoped to a named
future stage, not started), *Blocked* (external constraint).

| Capability | Status | Evidence |
|---|---|---|
| Overview (title, brand, vendor, category, tags) | Implemented and verified | `draft-product-editor.tsx` Overview tab; `PATCH /drafts/{id}`; `test_draft_editor.py`, `test_product_update.py` |
| Description editing (plain sanitized text) | Implemented and verified | Same write path; sanitizer + persistence covered in `test_draft_editor.py::test_patch_draft_persists_title_and_description` (script-tag stripping asserted inline) |
| Rich text or Markdown description editor | Missing | No rich-text/Markdown editor component anywhere in `frontend/`; description is a plain `<textarea>` (`draft-product-editor.tsx`) sending sanitized HTML-stripped text |
| Media preview, reordering, captions | Implemented and verified | `draft-media-panel.tsx` (reorder, featured, alt text, add-by-URL, remove); `POST/PATCH/DELETE /drafts/{id}/images`, `PATCH /drafts/{id}/images/reorder`; `test_draft_media_variants.py` |
| Image crop / watermark | Missing | `draft-media-panel.tsx`'s own docstring: *"File upload / crop land when S3 storage is wired; URLs keep Stage 4 unblocked."* No crop/watermark UI or endpoint exists |
| Variants (single-variant edit) | Implemented and verified | `draft-variants-panel.tsx`; `PATCH /drafts/{id}/variants/{variantId}`; covered in `test_draft_media_variants.py` and this pass's `TestNoAlternateDraftRouteBypassesTheGuard` |
| Bulk variant editing (a price/SKU rule applied across every variant at once) | Missing | No "apply to all"/bulk-selection code in `draft-variants-panel.tsx` (checked directly, zero matches); the write path is one `PATCH` per variant |
| Pricing and margin | Implemented and verified | `draft-pricing-panel.tsx`; `PricingEngine`; `GET/POST /drafts/{id}/pricing[/preview,/apply]`; extensive M23/M24A localized-pricing test suite (`test_pricing_*`) |
| Inventory | Implemented and verified | `draft-inventory-panel.tsx`; `app/api/v1/inventory/router.py` (`/sync`, `/sync-runs`, `/changes`) |
| Shipping / customs | Implemented and verified | `draft-shipping-panel.tsx`; shipping/customs fields on `Product` (migrations `0017`–`0022`), editable via the same `PATCH /drafts/{id}` path |
| SEO (meta title/description, tags, handle, score) | Implemented and verified | `draft-seo-panel.tsx`; `ProductRead.seo_title/seo_description/search_topics/slug`; `seo_score.py` backend-computed score; `GET /drafts/{id}/seo-score` |
| Tags | Implemented and verified | `tags` field on `ProductUpdateRequest`/`buildSavePayload`, comma-separated input on the Overview tab, split/trimmed both directions |
| AI Studio (side-by-side AI proposal review) | Deferred | The "AI Studio" tab exists and is reachable, but its own body reads *"Use Optimize with AI from More actions for now. Side-by-side proposal studio is Stage 6"* (`draft-product-editor.tsx`) — a placeholder pointing at the existing flow below, not a built feature |
| AI title/description optimisation (via version history, not AI Studio) | Implemented and verified | `ProductOptimizationService`, `StubProvider` (Phase 9) — output is clearly-synthetic placeholder text, not a real model, documented in `docs/PHASE_9_PLAN.md`; applies through a version-activate flow, separate from the deferred AI Studio tab above |
| Validation (schema-level: unknown fields, blank title, malformed/missing version token) | Implemented and verified | `extra="forbid"` on the shared schema; `_reject_blank_when_provided`; this pass's mandatory-`expectedUpdatedAt` 422 — all covered in `test_draft_editor_concurrency.py` |
| Publish readiness (score, issue checklist, "Fix N issues" gate) | Implemented but incomplete | `readinessFor()` (`editor-header/readiness.ts`) computes a score/issue list purely client-side and does disable the Publish button while issues remain — but **no automated test exercises `readinessFor()` or the readiness UI at all** (checked: zero matches for `readiness`/`readinessFor` across `tests/`), and the backend publish path (`ShopifySyncService.publish_product`) enforces only the currency-mismatch guard, not readiness — a direct API call could publish a product the UI would block |
| Optimistic concurrency on draft saves | Implemented and verified | `update_if_unmodified_since`; **mandatory** `expectedUpdatedAt` on the drafts path as of this acceptance-fix pass (previously optional); `TestOptimisticConcurrency`, `TestExpectedUpdatedAtIsMandatory` — see [M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md) |
| Safe conflict resolution (two-editor race) | Implemented and verified | Reload-with-confirmation and Review-with-explicit-overwrite, replacing the prior silent-refresh "Keep my changes" — 16 Playwright scenarios, both `chromium` and `mobile-chrome`; see [M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md) |
| Published product not editable via the drafts-only endpoint | Implemented and verified | `ProductService.update_draft` publish-state gate; `TestPublishedDraftIsNotEditableHere` |
| Premium editor shell (sticky header, tabs, Draft Preview, save-state indicator) | Implemented and verified | `draft-product-editor.tsx`, `editor-header/*`, `docs/PREMIUM_PRODUCT_EDITOR_UI.md` |

## 4. Supplier / variant mapping

| Capability | Status | Evidence |
|---|---|---|
| Supplier SKU / variant attribute capture on import | Existing and verified | `map_variants` (`app/integrations/aliexpress/mapper.py`), `TestImport::test_variants_and_images_are_stored` |
| Re-mapping a draft to a different supplier listing | Missing | No endpoint changes a product's `(source, external_id)` after creation |
| Multi-supplier price comparison for one product | Missing | No concept of more than one supplier per product |

## 5. Publishing

| Capability | Status | Evidence |
|---|---|---|
| Publish a draft to a connected Shopify store | Existing and verified | `docs/SHOPIFY_OAUTH_IMPLEMENTATION.md`; publish creates a `StoreListing`, which is what moves a row from Drafts to Products |
| Currency-mismatch guard before publish | Existing and verified | Blocks publish when variant/store currency disagree (`docs/ALIEXPRESS_LOCALIZED_PRICING.md`) |
| Publish to more than one store/channel simultaneously | Existing but incomplete | Data model supports multiple `StoreListing` rows per product; UI publish flow verified for one store at a time only |
| Publish to non-Shopify channels (Amazon, eBay, TikTok Shop, WooCommerce) | Missing | Only `app/integrations/shopify/` exists |

## 6. Synchronization

| Capability | Status | Evidence |
|---|---|---|
| Manual re-sync of price/stock/variants/images from supplier | Existing and verified | `POST /products/{id}/sync`, `TestSync` |
| Scheduled/automatic sync sweep | Existing and verified | `app/tasks/products.py` (`sweep_stale_products`), `tests/unit/test_product_tasks.py` |
| Merchant-edited fields protected from being overwritten by sync | Existing and verified | `_SYNCED_FIELDS` divergence check in `_upsert` |
| Inventory sync run history / change log | Existing and verified | `app/api/v1/inventory/router.py` (`/sync`, `/sync-runs`, `/changes`) |
| Automation rules (price/stock-triggered actions) | Existing and verified | `app/api/v1/automation/router.py` (rules CRUD + runs) |

## 7. Orders & fulfilment

| Capability | Status | Evidence |
|---|---|---|
| Sync orders in from Shopify | Existing and verified | `OrderSyncService.sync_orders`, `app/api/v1/orders/router.py` |
| Order timeline / statistics | Existing and verified | `OrderSyncService.get_timeline`/`statistics` |
| Auto-place the matching order with the supplier | Blocked by external API/permission | `app/integrations/aliexpress/orders.py` docstring: this account is not a registered AliExpress DS publisher; the populated order-detail body has *"never been observed live"*; `docs/TECHNICAL_DEBT.md` M16. Placing a real order also spends real money — explicitly out of scope for automated verification per this platform's legal/integration constraints (no browser-automated purchasing) |
| Push tracking numbers back to the sales channel | Missing | No fulfilment-creation call to Shopify exists in `app/integrations/shopify/` |
| Order splitting across multiple supplier shipments | Missing | No concept of partial/split fulfilment |

## 8. Tracking

| Capability | Status | Evidence |
|---|---|---|
| Ingest supplier tracking numbers | Missing | No tracking-number field on `Order`/fulfilment models |
| Tracking-status aggregator (17Track-style) | Missing | No such integration |
| Customer-facing tracking page | Missing | No such route |

## 9. Supplier optimization

| Capability | Status | Evidence |
|---|---|---|
| Find-a-cheaper-supplier / re-price suggestions | Missing | No cross-supplier comparison exists |
| Automated repricing rules | Existing and verified (price/stock triggers only, not multi-supplier) | `app/api/v1/automation/router.py` |
| Supplier reliability scoring | Missing | No such feature |

## 10. Multi-channel / operations

| Capability | Status | Evidence |
|---|---|---|
| Multiple connected Shopify stores per tenant | Existing and verified | `app/api/v1/stores/router.py`, `Store` model supports many rows per tenant |
| Cross-store analytics dashboard | Existing and verified | `app/api/v1/analytics/router.py` (`/dashboard`) |
| Team roles/permissions | Existing and verified | `RoleName` enum, `RequireAdmin`/role-based deps (Phase 1/2 auth work) |
| Non-Shopify channel connections | Missing | See Publishing §5 |

---

## Notes on method

This matrix was built by reading routers (`backend/app/api/v1/*/router.py`),
services (`backend/app/services/`), the AliExpress/Shopify integration
packages, and the frontend route tree (`frontend/app/(protected)/`) — not by
asking what *should* exist. Every "Missing" row was checked by searching for
the capability's obvious name across `app/` and `frontend/` and finding
nothing, not by assumption. Where a limitation is inherent to the supplier's
public API rather than this codebase, that is stated as such rather than
recorded as a bug.
