# EBAY-C3 — Draft → eBay listing

Status: **implemented; verified against a mocked eBay transport only.** No
listing has been created on a real eBay account (owner step: connect a
seller, save listing setup, publish one draft).

Built to the approved proposal [`EBAY_C3_PROPOSAL.md`](EBAY_C3_PROPOSAL.md)
(D-C3-1 … D-C3-4 approved by the owner on 2026-10-03; D-C3-5 scope limits
applied as written).

## What a merchant does

1. Integrations → eBay → **Listing setup**: choose policies and warehouse for
   a marketplace and save. The first save creates that marketplace's
   **eBay store** (D-C3-2), e.g. "eBay United States", currency USD.
2. Draft editor → **Review & publish** → **eBay details**: ask eBay for
   category suggestions (from the title, or typed words), pick one, fill the
   item specifics eBay marks required, save.
3. Choose the eBay store in the store selector. The readiness check runs
   against eBay's rules; **Publish to Store** creates the listing.

## How it works

| Step | Call | Notes |
|---|---|---|
| Category suggestions, aspects | Taxonomy API with an **application token** (client-credentials grant) | Token held in process memory only; no new seller scope (D-C3-3) |
| Inventory item | `PUT /sell/inventory/v1/inventory_item/{sku}` | SKU `dp-{product id}`, deterministic |
| Offer | `GET offer?sku=` → `POST offer` or `PUT offer/{id}` | An existing offer is adopted, never duplicated |
| Publish | `POST offer/{id}/publish` | Skipped when the offer is already published; the update reaches the live listing |
| Record | `store_listings` row: listing id, offer id, SKU, URL | eBay rows are declared and erased under eBay's deletion contract |

Readiness (D-C3-1) is one service with per-channel checks chosen by the store:
eBay connected, listing setup saved for the store, title ≤ 80, at least one
https image, one enabled variant, a `sell_price` in the marketplace currency
(D-C3-4; the supplier `list_price` is never used), stock > 0, a category, and
every required aspect — re-checked against eBay's current requirements. The
publish runs the same check under the product row lock first.

## Data

| Table | Change | Erasure |
|---|---|---|
| `product_marketplace_attributes` (0038) | new, tenant-scoped: category + aspects per product and marketplace | product data, not seller data |
| `ebay_listing_defaults.store_id` (0039) | new nullable FK, SET NULL | rows erased with the connection (C2) |
| `store_listings.external_offer_id`, `external_sku` (0039) | new nullable columns | eBay rows: `EbayStoreListingsOwner` on deletion; physically deleted on disconnect |

Disconnect now marks the eBay stores disconnected and deletes their listing
rows; listings already live on eBay are not touched.

## Decisions taken while building

- **A failed publish writes nothing.** The request's transaction rolls back on
  the error, so an ERROR state written before re-raising would not survive.
  eBay's own reason (its `errors[].message`, at most three) is returned to the
  merchant as `ebay_listing_rejected` (422).
- **Listing text is the draft's own** title and description. An approved AI
  version reaches eBay only after it has been applied to the draft; the
  Shopify-only "keep the AI version live" rule does not apply to eBay.
- **No ERROR/unknown listing state for timeouts.** A retried publish adopts the
  inventory item and offer the first attempt created, which is what makes a
  timeout safe to retry.

## Known limitations

- Single-variant products only; multi-variant needs inventory item groups.
- Condition is always NEW. Shipping and returns come from the chosen policies.
- The application token and category tree ids are cached per process.
- eBay's Taxonomy suggestions in the **sandbox** return eBay's sample data.

## Verification

- Backend: unit tests for the Taxonomy parsers and aspect rules; integration
  tests (mocked transport) for category/aspects (7) and publish (10): store
  creation, readiness blockers, full publish payloads, adopt-on-retry, eBay
  refusal, blocked product never reaching eBay, roles, Shopify readiness
  refusing an eBay store, disconnect erasure, deletion owner.
- Frontend: Playwright (route-mocked) — eBay details flow and publish routed to
  the eBay endpoints.
- **Not verified:** any call to real eBay.
