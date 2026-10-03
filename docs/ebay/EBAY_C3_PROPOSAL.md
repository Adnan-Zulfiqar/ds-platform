# EBAY-C3 — Draft → eBay listing: architecture proposal

Status: **PROPOSAL — awaiting owner approval. Nothing here is implemented.**
Written under CLAUDE.md §12 rule 1 ("never rewrite existing architecture
without approval — propose, explain, wait") and the owner's hard stop 6.
EBAY-C4 (inventory/price sync), C5 (orders) and C6 (hardening) all build on
the listing records C3 creates, so they wait on the same approval.

## What C3 must do

Publish one draft to eBay as a fixed-price listing, using the C2 defaults:

1. `PUT /sell/inventory/v1/inventory_item/{sku}`: title, description,
   images, condition, item specifics (aspects), quantity.
2. `POST /sell/inventory/v1/offer`: sku, marketplace, `FIXED_PRICE`,
   category id, price + currency, the three policy ids, `merchantLocationKey`.
3. `POST /sell/inventory/v1/offer/{offerId}/publish` → `listingId`.

Steps 1–2 are idempotent by SKU and offer lookup, so a retry adopts the
existing item and offer, as Shopify publish adopts by handle.

## Decisions that need approval (the architecture changes)

### D-C3-1 — Publish readiness becomes multi-channel

`PublishReadinessService` is Shopify-only today: `CHANNEL_SHOPIFY` is the
only channel, any other platform yields `unsupported_channel`, and it imports
`ShopifySyncService` for its currency check. eBay needs its own blockers:
C2 defaults missing, no category, required aspects missing, no price in the
marketplace currency.

**Proposal:** keep the shared checks (title, description, images, price) in
the service, and move platform checks behind a small per-channel checker
(`ShopifyReadiness`, `EbayReadiness`) chosen by `store.platform`. This
changes the shape of an existing module. Alternative: a separate
`EbayPublishReadiness`, which duplicates the shared checks (rejected
unless preferred: CLAUDE.md "never duplicate").

### D-C3-2 — How an eBay "store" exists

`store_listings` is unique on `(tenant, store, product)`, and the editor
picks a `Store`. `StorePlatform.EBAY` exists, but no `Store` row is tied to
an `ebay_connections` row (Shopify uses `shopify_connections.store_id`).

**Proposal:** one `Store` (platform `ebay`) per tenant and marketplace,
created when C2 defaults are first saved for that marketplace, linked by a
new nullable `ebay_listing_defaults.store_id`. Listings then reuse
`store_listings` (new nullable columns: `external_offer_id`,
`external_sku`; `external_product_id` holds the eBay `listingId`). These are
schema changes to existing tables (CLAUDE.md §12 rule 7: announced, own
migration).

**And** `store_listings` rows for eBay hold the seller's listing ids, so
they must be declared and erased (master roadmap release guard): a
`StoreListing` owner that erases eBay rows by connection.

### D-C3-3 — Category and item specifics

eBay rejects an offer without a leaf `categoryId`, and most categories
require aspects (Brand, Type, …). The product model has no structured
attributes (`ProductVariant.external_attributes` is a string).

**Proposal:**
- Category: `GET /commerce/taxonomy/v1/category_tree/{id}/get_category_suggestions`
  from the title; the merchant confirms or picks. The Taxonomy API needs an
  **application** token (client-credentials grant) — a new token path beside
  C1's user token, cached in Redis, no new scope for sellers.
- Aspects: new `product_marketplace_attributes` table (tenant-scoped,
  `product_id`, `marketplace_id`, `category_id`, `aspects` JSONB). New table +
  editor UI section "eBay details" listing required aspects from
  `get_item_aspects_for_category`.

### D-C3-4 — Price and currency

eBay prices in the marketplace currency (USD on EBAY_US, GBP on EBAY_GB…).
The pricing engine resolves a non-Shopify store's currency from
`store.currency` unverified. **Proposal:** the eBay `Store` carries the
marketplace currency fixed at creation (it cannot drift the way a Shopify
shop's can), and publish refuses a variant priced in another currency, as
Shopify publish does.

### D-C3-5 — Scope limits for the first cut

Single-variant products only (multi-variant needs eBay inventory item groups);
condition `NEW`; quantity from `stock_quantity`. Multi-variant is a follow-up.

## What happens without approval

Nothing in C3–C6 is started. C2 (listing setup) is complete and merged on its
own. The decisions above are the smallest set that makes an eBay listing
possible; each can be changed before any code is written.
