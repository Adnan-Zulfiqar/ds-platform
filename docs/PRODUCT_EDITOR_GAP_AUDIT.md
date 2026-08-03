# Product editor gap audit

Audits the existing AliExpress → catalogue pipeline against what a full,
editable AutoDS-style product workspace needs. Every finding below is
evidence-based: read from the current code and, for the AliExpress contract
specifically, verified against the real captured fixture
(`backend/tests/fixtures/aliexpress/product.json`) rather than assumed from
documentation. No code changed while writing this document.

---

## 0. Stage progress

**Stage 1 — AliExpress description import and sanitization: done (2026-08-03).**
Closes the "Full description" / "HTML description" / "Plain-text
description" rows in §2 below: `detail`/`mobile_detail` are now parsed
(`app/integrations/aliexpress/catalog.py`), sanitized on import
(`app/core/sanitize.py`, `nh3` allowlist), stored in `Product.description`
(merchant-editable) and the new `Product.supplier_description` (always-fresh
supplier snapshot, migration `0013`), exposed on `ProductDetailRead`, and fed
to `ProductOptimizationService` as plain text instead of empty string. The
supplier-vs-merchant overwrite-protection mechanism is built but not yet
exercised by a real edit — see `docs/TECHNICAL_DEBT.md`. Every other row in
§2 is unchanged from the original audit; stages 2+ remain as scoped in §4.

---

## 1. Method

Files read in full: `backend/app/integrations/aliexpress/catalog.py` (wire
models + parsers), `backend/app/integrations/aliexpress/mapper.py`,
`backend/app/services/product_import.py`, `backend/app/services/product_optimization.py`,
`backend/app/models/product.py`, `backend/app/models/shopify.py` (`StoreListing`),
`backend/app/repositories/product.py`, `backend/app/schemas/product.py`,
`backend/app/api/v1/products/router.py`, `frontend/components/products/product-table.tsx`,
`frontend/types/api.ts` (`Product`), and the `frontend/app/(protected)/products/`
route tree.

The fixture check that matters most: `ItemBaseInfo` (the wire model for
`aliexpress.ds.product.get`'s core fields) does not declare `detail` or
`mobile_detail`. Rather than trust the module's own docstring claim that these
exist on the wire, they were checked directly against the committed fixture:

```
detail          -> present, 716 chars, HTML (<div class="detailmodule_html">...)
mobile_detail   -> present, 796 chars, JSON module list
```

Both are real, both arrive on every real `product.get` call, and neither is
parsed, mapped, stored, or exposed anywhere in this codebase today. This is
the single largest finding and it recurs through most of §2's rows.

---

## 2. Field-by-field audit

Legend: ✅ yes · ❌ no · ⚠️ partial/misleading · — not applicable.

| Field | AliExpress returns it? | Parsed by schema? | Mapped? | Stored in DB? | Returned by API? | In UI? | Editable? | Notes |
|---|---|---|---|---|---|---|---|---|
| Title | ✅ `subject` | ✅ `ItemBaseInfo.title` | ✅ `map_product` → `title` | ✅ `Product.title` | ✅ `ProductRead.title` | ✅ table | ❌ | No PATCH endpoint exists at all (see §3.4). |
| Full description | ✅ `detail` (HTML), verified in fixture | ❌ not a field on `ItemBaseInfo` | ❌ `map_product` never reads it | ⚠️ `Product.description` column **exists** but is always `NULL` for every AliExpress import | ❌ deliberately excluded (`schemas/product.py` docstring: "no sanitiser yet") | ❌ | ❌ | **Biggest gap.** Column exists, is documented as "supplier description," and is silently never populated. `ProductOptimizationService._build_variables` already reads `product.description` as AI input (`app/services/product_optimization.py:201`) — every real optimisation today runs on an empty string. |
| HTML description | ✅ `detail` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | Same field as above; AliExpress's `detail` **is** HTML. No sanitiser exists anywhere in the codebase (`bleach`/`nh3`/similar not a dependency). |
| Plain-text description | — (not sent separately) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | Would need to be derived from `detail` after sanitisation; nothing does this. |
| Images | ✅ `image_urls` (`;`-delimited) | ✅ `MultimediaInfo.urls` | ✅ `map_images` | ✅ `product_images` table | ✅ `ProductDetailRead.images` | ⚠️ only a count/thumbnail is implied — the products table shows no image at all | ❌ | Read/stored/returned; no reorder, delete, upload, or alt-text anywhere. |
| Featured image | ⚠️ implied by order | ✅ (`position` order) | ✅ position 0 | ✅ `position` column | ✅ (ordered list) | ❌ | ❌ | No explicit "featured" flag — convention only (first by `position`). |
| Variant images | ✅ `sku_image` per property | ✅ `Sku.image_url` (first found) | ✅ `map_variants` → `image_url` | ✅ `product_variants.image_url` | ✅ `ProductVariantRead.image_url` | ❌ (variants not shown in UI at all) | ❌ | Only the *first* property image is kept; a SKU with per-property images beyond the first loses the rest. |
| Variant option names | ✅ `sku_property_name` | ✅ `SkuProperty.sku_property_name` | ⚠️ flattened into `label` string only | ❌ no discrete column | ❌ | ❌ | ❌ | "Color: Beige / Material: CANVAS" is stored as one opaque string (`ProductVariant.label`). Editing "Color" separately from "Beige" is not possible without parsing that string back apart. |
| Variant option values | ✅ `sku_property_value` | ✅ | ⚠️ flattened (see above) | ❌ | ❌ | ❌ | ❌ | Same gap as option names. |
| SKU | ✅ `sku_id` | ✅ | ✅ → `external_variant_id` | ✅ | ✅ `ProductVariantRead.external_variant_id` | ❌ | ❌ | This is the *supplier's* SKU id, used verbatim because AliExpress orders require it exactly. There is no separate **merchant-editable** SKU field. |
| Barcode | ❌ not returned by this endpoint | — | — | ❌ no column | ❌ | ❌ | ❌ | Genuine AliExpress API limitation, not an oversight — `ds.product.get` has no barcode/UPC/EAN field. |
| Supplier price | ✅ `sku_price` / `offer_sale_price` | ✅ `Sku.list_price` / `sale_price` | ✅ → `cost_price_min/max` (product), `cost_price`/`list_price` (variant) | ✅ | ✅ | ✅ table shows the range | ❌ | Decimal end-to-end, never float — correct today. |
| Variant cost | ✅ | ✅ | ✅ `map_variants` → `cost_price` | ✅ `product_variants.cost_price` | ✅ | ❌ | ❌ | Per-variant cost exists in the DB; nothing surfaces it per-variant in the UI (only the product-level range). |
| Selling price | — (merchant sets this) | — | — | ⚠️ `Product.sell_price` exists, **product-level only** | ❌ not in `ProductRead` at all | ❌ | ❌ | No **per-variant** selling price column exists anywhere. No `PATCH` to set even the product-level one. |
| Compare-at price | — | — | — | ❌ no column, product or variant | ❌ | ❌ | ❌ | Does not exist at any level. Needed for Step 8's pricing workspace. |
| Inventory | ✅ `sku_available_stock` | ✅ | ✅ → `stock_quantity` (product = sum, variant = per-SKU) | ✅ both levels | ✅ both levels | ✅ product-level total in table | ❌ | See §3.2 — the table's `436` is the **sum across variants**, not "available for sale" in any other sense; nothing in the UI labels it as such. |
| Weight | ✅ `gross_weight` | ✅ `PackageInfo.weight_kg` | ❌ explicitly not mapped (`mapper.py:52` docstring) | ❌ | ❌ | ❌ | ❌ | Parsed, then discarded on purpose ("nothing consumes them until shipping estimates arrive"). |
| Dimensions | ✅ `package_length/width/height` | ✅ `PackageInfo` | ❌ | ❌ | ❌ | ❌ | ❌ | Same as weight — parsed, discarded. |
| Category | ✅ `category_id` (detail call); `+name` on feed only | ✅ | ✅ `category_id` always; `category_name` only via feed import | ✅ both columns exist | ✅ | ✅ id/name shown when present | ❌ | A product imported via **detail** (the normal single-product import path) gets `category_id` but no `category_name` — only feed-browsed imports get a name, because only the feed payload carries one. A dedicated `ds.category.get` lookup would be needed to backfill names for detail-imported products. |
| Brand | ✅ via `ae_item_properties` (`attr_name == "Brand Name"`) | ✅ | ✅ `map_product` scans attributes | ✅ `Product.brand` | ✅ | ✅ | ❌ | Correctly derived; only present when the supplier tagged a brand attribute. |
| Supplier/store name | ✅ `ae_store_info.store_name` | ✅ `StoreInfo.store_name` | ✅ | ✅ `Product.supplier_name` | ✅ | ✅ | ❌ | Also carries ratings (`Product.rating`, `review_count`, `order_count`) — all mapped and shown. |
| Supplier URL | ❌ not returned by this endpoint | — | — | ❌ | ❌ | ❌ | ❌ | `ae_store_info` has no store URL field. Would need a separate storefront-URL construction (`aliexpress.com/store/{store_id}`) or a different endpoint — currently invented nowhere, correctly. |
| Product URL | ⚠️ not returned directly; constructed | ✅ `ProductDetail.product_id` | ✅ `mapper.py:_ITEM_URL.format(...)` | ✅ `Product.external_url` | ✅ | ❌ not linked from UI | ❌ | Built from a known-stable URL pattern, not sent by AliExpress. Reasonable, but the assumption is worth stating rather than implying it came from the supplier. |
| Tags | ❌ not returned by this endpoint | — | — | ✅ `Product.tags` (JSONB, Phase 9 stage 3) | ✅ `ProductRead.tags` | ❌ | ❌ | Column exists for **merchant-assigned** tags; AliExpress never supplies any, so it is always `[]` on import — correct, but there is no UI to ever set one. |
| Attributes/specifications | ✅ `ae_item_properties` (list of name/value) | ✅ `ItemProperty` | ⚠️ only `Brand Name` is extracted; the rest of the list is read then discarded | ❌ no table/column for the full attribute list | ❌ | ❌ | ❌ | A product with 15 spec rows (material, compatibility, size chart, …) keeps exactly one (brand) after import. |
| Shipping data | ✅ `logistics_info_dto` (`delivery_time`, `ship_to_country`) | ✅ `LogisticsInfo` | ❌ never read by `map_product` | ❌ | ❌ | ❌ | ❌ | Parsed by the wire model, never touched by the mapper at all (not even discarded-with-a-comment like weight/dimensions — just unused). |
| Processing time | ❌ not part of `logistics_info_dto` on this endpoint | — | — | ❌ | ❌ | ❌ | ❌ | Not present in the captured contract; would need verification against a live call before claiming otherwise. |
| Currency | ✅ `currency_code` (base + per-SKU) | ✅ | ✅ | ✅ `Product.currency`, `ProductVariant.currency` | ✅ | ✅ | ❌ | Correct at both levels. |
| Product status | — (DropPilot's own concept) | — | — | ✅ `ProductStatus` enum: `draft/active/archived/unavailable` | ✅ | ✅ | ❌ (no direct status-change endpoint) | See §3.1 — this enum does **not** match the 8-state lifecycle the brief asks for (no `ready_to_publish`, `published` distinct from `active`, `import_failed`, `sync_failed` as product-level states — those two live only on `ProductImport.status`, a separate table). |
| SEO title | — | — | — | ✅ `Product.seo_title` (Phase 9 stage 3) | ✅ | ❌ no SEO tab exists | ❌ | Column and API field exist; no UI surface at all yet. |
| SEO description | — | — | — | ✅ `Product.seo_description` | ✅ | ❌ | ❌ | Same as above. |
| URL slug | — | — | — | ✅ `Product.slug`, unique per tenant (`uq_products_tenant_slug`) | ✅ | ❌ | ❌ | Column + constraint exist; nothing ever sets it (no import default, no editor). |
| AI status | — | — | — | ✅ `ProductAIStatus` | ✅ | ✅ badge in table | ❌ (system-managed) | Correctly system-derived from version activation, not meant to be directly editable. |
| Optimization history | — | — | — | ✅ `product_versions` table | ✅ `GET /{id}/versions`, `.../activate` | ✅ history sheet exists (Phase 9 stage 3) | ✅ activate/rollback works | The one area that is genuinely complete end-to-end already — see §3.5 for its current scope limit (title + description only). |

---

## 3. Findings by theme

### 3.1 Product lifecycle does not match the requested states

Current `ProductStatus`: `draft`, `active`, `archived`, `unavailable`
(`backend/app/models/product.py:62`). The brief's requested lifecycle —
Imported, Draft, Editing, Ready to Publish, Published, Archived, Import
Failed, Sync Failed — does not map cleanly onto this:

- **Imported** is correctly an *event*, not a state: every successful import
  writes a `ProductImport` row (`ImportStatus.SUCCEEDED`) and leaves the
  product itself in `DRAFT`. `Product.last_synced_at` already exists as the
  "when was this last imported/refreshed" timestamp. Recommend keeping this
  exactly as-is — it already matches the brief's own recommended approach
  (`status=draft`, retain `imported_at`-equivalent, import history separate).
- **Editing** has no representation — there is no draft-of-a-draft or
  optimistic lock. Out of scope unless real-time collision editing is
  requested; a simple `updated_at` + "last edited by" is enough for a single
  editor per tenant, which is the realistic case here.
- **Ready to Publish** does not exist. This is the validator's job (Step 12)
  and should be a *computed* value from a readiness check, not a stored
  status a human sets — otherwise it goes stale the moment a variant's price
  is cleared.
- **Published** is not distinct from `active` today. `StoreListing.status`
  (`ListingSyncStatus`: `pending/synced/error/removed`) already tracks
  per-channel publish state (see §3.3) — "published" is more accurately a
  property of a `StoreListing` row than of the product itself, since a
  product can be published to one channel and not another.
- **Import Failed / Sync Failed** already exist, but on `ProductImport`
  (`error_code`, `error_message`), not on `Product`. This is arguably
  correct — a product that imported fine six months ago and fails today's
  refresh should not flip to an unsellable status retroactively — but it
  means "why does this product look stale" requires checking a second table,
  which the editor's supplier panel should surface directly (`last_sync_error`
  already exists on `Product` for exactly this).

**Recommendation:** do not replace `ProductStatus`. Add what is missing
(a computed readiness field in the API response, not a new enum value) and
surface `last_sync_error` / `ProductImport` history in the editor's supplier
panel, rather than growing the enum to match every requested label 1:1.

### 3.2 The `436` stock figure is ambiguous today

`ProductRead.stock_quantity` is `Product.stock_quantity`, which
`map_product` sets to `detail.total_stock` — the **sum of every variant's
`sku_available_stock`** (`catalog.py:325`). It is:

- Not "available stock" in the sense of reserved-vs-free (no reservation
  concept exists in this codebase for catalogue stock — reservations exist
  only in `order.py` for placed orders).
- Not cached separately from live — there is no separate "last known" vs
  "just fetched" distinction; it *is* the just-fetched number, current as of
  `last_synced_at`.
- Not per-variant in the table (each `ProductVariant.stock_quantity` exists
  and is correct individually; the table only shows the product-level sum).

The brief's request to label this clearly (supplier total / variant sum /
available / cached) is a real, valid ask — today it is silently "sum of
variant stock as of last sync" with no label saying so.

### 3.3 Channel publishing already has most of what Step 15 asks for

`StoreListing` (`backend/app/models/shopify.py:95`) already is the
`product_channel_publications` entity the brief lists as a "potential" new
table: product↔store mapping, external ids, per-variant id/inventory maps,
`ListingSyncStatus` (`pending/synced/error/removed`), `last_synced_at`,
`last_error`. **Do not create a second table for this** — extending
`StoreListing` (e.g. a `published_at` column if the four existing statuses
prove insufficient) is the correct move if anything is needed at all.

`ShopifySyncService.publish_product` (already audited and fixed for
idempotency this session — deterministic handle, `_create_or_adopt`) is the
adapter to reuse for Step 13's Shopify publishing panel. eBay/TikTok/
WooCommerce adapters do not exist in any form — the publishing panel must
show them as literally not implemented, not "Coming soon" dressed up as a
channel state, to avoid the exact honesty violation the brief warns against.

### 3.4 There is no write path for a product at all

Confirmed by reading the full router (`backend/app/api/v1/products/router.py`,
315 lines) and schema file (`backend/app/schemas/product.py`, 273 lines):
every endpoint is `GET`, or a `POST` that either imports/re-imports
(overwrites from the supplier) or generates/activates an AI version. There is:

- No `ProductUpdateRequest` schema.
- No `PATCH /products/{id}`.
- No variant update endpoint or schema.
- No image endpoints (create/reorder/delete) at all — `ProductImageRepository`
  exists and is written by the importer, but nothing outside `product_import.py`
  calls it.
- No publish-readiness or publish endpoint at the product level (Shopify's own
  `/integrations/shopify/publish` exists but is not gated by any validator).

This is the structural reason "the merchant cannot open the product and fully
edit it" — not a missing UI route alone, but a genuinely absent write API
underneath it. The existing pattern (`BaseService` + `TenantScopedRepository`
+ thin router, exactly as `ShopifyService`/`ProductOptimizationService`
already demonstrate) extends cleanly to add one; nothing needs rearchitecting.

### 3.5 A re-sync silently overwrites fields with no merchant-edit concept

`ProductImportService._upsert` (`product_import.py:155`) `setattr`s every
field the mapper returns onto the existing row on every re-sync/refresh,
unconditionally — `title`, `brand`, `currency`, `cost_price_min/max`,
`stock_quantity`, `supplier_name`, `rating`, `review_count`, `order_count`.
`status` is the *only* field explicitly preserved across a re-sync.

There is currently no mechanism by which this could destroy a merchant edit,
for the simple reason that **no merchant edit is possible yet** (§3.4) — but
the moment a `PATCH` endpoint exists, the next scheduled or manual re-sync
will silently overwrite it, exactly the failure mode the brief warns against
("Do not overwrite the original supplier snapshot"). This needs solving
*before or alongside* the `PATCH` endpoint, not after — the two are the same
piece of work. The clean fix is the same shape `ProductVersion` already
uses for AI content: keep the supplier's own values in dedicated
`supplier_*`-prefixed columns (or reuse `ProductVersion` with
`source=ORIGINAL`, updated on each sync, distinct from an
`source=MANUAL_EDIT` version the merchant's changes create) rather than
letting merchant edits live in the same columns a sync overwrites.

### 3.6 AI optimisation covers two fields; the brief asks for eleven

`ProductOptimizationService.optimize_product` (`app/services/product_optimization.py:66`)
generates exactly `title` and `description` via two seeded prompts
(`product_title_generator`, `product_description_generator`), storing both in
one `ProductVersion.content` JSONB blob. The brief's Step 11 list — bullet
points, SEO title/description, tags, highlights, benefits, FAQ, readability —
has no prompts, no generation call, and no version-content shape for any of
them today. `ProductVersion.content` is JSONB specifically so this can grow
without a migration (`models/product.py:496` docstring already says so) —
extending it is additive, not a rework.

Also confirms: every generation still runs through `StubProvider` only
(`docs/TECHNICAL_DEBT.md` M19, unchanged) — any new generator added here
inherits the same "clearly synthetic, not live" requirement the brief states
explicitly for Step 11.

### 3.7 Frontend: no detail route, no edit affordance

`frontend/app/(protected)/products/` contains only `page.tsx` (the list) —
no `[productId]/page.tsx` exists anywhere in the tree. `ProductTable` renders
`product.title` as plain text (`product-table.tsx:125`), with no link, no
"Edit Product" button, and no per-row navigation at all. The only per-row
actions are `OptimizeProductButton` and `ProductVersionHistorySheet`, both of
which already correctly use `productId` as a prop and could sit unchanged
next to a new "Edit Product" affordance without rework.

---

## 4. What this means for the implementation stages

Nothing here requires rebuilding an existing system — every gap is an
**extension point** on architecture already in place and already proven
(the same `BaseService`/`TenantScopedRepository`/thin-router/React-Query
pattern used throughout Shopify, AI prompts, and Phase 9 stage 3). In rough
dependency order:

1. **Description import** (§3.2 of the brief) — parse `detail`/`mobile_detail`
   into `ItemBaseInfo`, sanitise, populate `Product.description`. Unblocks
   AI optimisation actually having input text, and is a one-file mapper
   change plus a sanitiser dependency — no migration needed (`description`
   column already exists).
2. **Supplier-snapshot vs merchant-edit separation** (§3.5) — must land
   before or with the first `PATCH`, not after.
3. **Write API**: `PATCH /products/{id}`, variant update, image endpoints —
   the structural blocker for everything else (§3.4).
4. **Frontend detail route + editor shell** — needs (3) to have something to
   call.
5. **Variant structuring** (discrete option name/value, per-variant sell
   price, compare-at price) — a migration, additive to `product_variants`.
6. **Pricing workspace, inventory workspace, publish-readiness validator** —
   built on top of (3) and (5).
7. **AI generator expansion** (§3.6) and **publishing panel reusing
   `ShopifySyncService`** (§3.3) — parallel, independent extensions once (3)
   exists.

No step here requires a new product/variant/image table, a new channel-
publication table, or a parallel product system — confirming the brief's own
instruction ("do not create a duplicate product system") is achievable by
extension alone.
