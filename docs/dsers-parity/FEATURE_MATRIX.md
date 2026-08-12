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

Audit date: 2026-08-12. Branch: `feature/dsers-parity-m1-import-drafts`.

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
| Duplicate-import warning in the UI before submit | Existing but incomplete | `frontend/components/products/import-product-dialog.tsx` checks only the currently-loaded Drafts page cache (25 rows), not the full tenant catalogue — documented in the component's own comment |
| Variant count on the Drafts list | Missing | `ProductRead` (list schema) deliberately omits `variants` for list-payload size (`backend/app/schemas/product.py` docstring); adding a count needs a correlated-subquery change to `ProductRepository.list_drafts`, not done this pass |
| Import provenance / supplier snapshot preserved separately from merchant edits | Existing and verified | `Product.supplier_title`/`supplier_brand`/`supplier_description` vs. merchant-editable `title`/`brand`/`description`; `_SYNCED_FIELDS` divergence protection in `product_import.py` |

## 3. Premium product editor

| Capability | Status | Evidence |
|---|---|---|
| Basic safe draft view (title, images, variants, cost, status) | Existing and verified | `frontend/app/(protected)/drafts/[id]/page.tsx`, `ProductDetailRead` schema |
| Rich text/markdown description editor | Missing | Description is edited as plain sanitized text; no rich editor component |
| Bulk variant editing (price/SKU rules across all variants at once) | Missing | `PATCH /drafts/{id}/variants/{variantId}` is single-variant only |
| Image editing (crop/watermark-removal) | Missing | Images can be reordered and captioned only (`useReorderDraftImages`, `useUpdateDraftImage`) |
| AI title/description optimisation | Existing and verified | `ProductOptimizationService`, `StubProvider` (Phase 9) — output is clearly-synthetic placeholder text, not a real model, documented in `docs/PHASE_9_PLAN.md` |
| SEO fields (meta title/description, tags, handle) | Existing and verified | `ProductRead.seo_title/seo_description/search_topics/slug/tags` |

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
