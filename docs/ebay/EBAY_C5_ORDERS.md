# EBAY-C5 — Orders, fulfilment, tracking, cancellation

Status: **implemented; verified against a mocked eBay transport only.**

## What a merchant does

- **Orders → Import eBay orders**: reads eBay orders changed in the last
  seven days (Fulfillment API `getOrders`) into DropPilot's orders, one row
  per eBay order — importing again updates, never duplicates. Line items find
  their product through the C3 SKU `dp-<product id>`; the order is attached to
  the marketplace's eBay store.
- **Order → Mark shipped on eBay**: carrier and tracking number are sent for
  every line item (`createShippingFulfillment`), a shipment is recorded and
  the order becomes *shipped*. Sending the same tracking number again sends
  nothing; the order row is locked first, so two concurrent requests cannot
  both send.
- **Cancellation**: an order eBay reports cancelled is shown cancelled and can
  no longer be marked shipped.

## Status mapping

| eBay | DropPilot |
|---|---|
| `cancelStatus.cancelState = CANCELED` | cancelled |
| payment `FULLY_REFUNDED` | refunded |
| fulfilment `FULFILLED` | shipped |
| fulfilment `IN_PROGRESS` | processing |
| payment `PAID` (not started) | paid |
| otherwise | awaiting payment |

## Data and eBay's deletion contract

New: `order_source` value `ebay` and `orders.marketplace_buyer_username`
(migration `0040`). Stored buyer data is what fulfilment needs: recipient
name, phone, shipping address — plus the buyer's username.

eBay's order data identifies a buyer **only by username** (there is no
immutable buyer id in it), so `EbayOrderBuyersOwner` matches on the username,
exactly and case-sensitively, and **anonymises** the order (name, phone,
address and username set to NULL; amounts, items and dates kept as the
merchant's sales record). Trade-off, written down rather than hidden: a buyer
who renamed before the notice is missed; a later buyer who takes a released
name could have the earlier buyer's orders anonymised — data removed, never
exposed. Declared in `EBAY_STORAGE_DECLARATIONS`; the governance tests require
it.

Disconnecting eBay keeps imported orders (they are the merchant's records,
as with Shopify), and the username stays so a later buyer notice still finds
them.

## Not in this milestone

- ~~Scheduled import~~ — added after owner approval (B-011, D-012):
  `ebay.import_orders_all` runs hourly and imports the last two days per
  connected workspace; on-demand import stays.
- **Seller-initiated cancellation** (eBay Post-Order API) and returns.
- Supplier ordering for eBay orders uses the existing order flows; nothing
  eBay-specific was added there.

## Verification

- Integration (mocked transport): import maps statuses, store, product and
  buyer fields; re-import updates and reflects a cancellation without
  duplicating items; shipping tells eBay once per tracking number; a
  cancelled order cannot be shipped; the deletion owner anonymises by
  username, keeps the sale, and is idempotent; no connection → 409.
- Playwright (route-mocked): import button for admins only; the ship form
  sends exactly carrier and tracking.
- **Not verified:** any real eBay order.
