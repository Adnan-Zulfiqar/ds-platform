# Track E7 W5 — mark a WooCommerce order shipped

Status: **implemented; verified against a faked WooCommerce API only.**

## What it does

On a WooCommerce order's page, an owner or admin enters the carrier, the
tracking number, an optional https tracking link, and whether the customer
sees the note. That calls
`POST /integrations/woocommerce/orders/{id}/shipments`, which uses the same
request and response as the Shopify fulfilment (E1).

DropPilot then:

1. Sets the WooCommerce order to **`completed`** (`PUT /orders/{id}`).
2. Adds an **order note**: "Shipped with \<carrier>, tracking number \<n>.
   Track it: \<link>". `customer_note` follows the merchant's choice.
3. Records a `Shipment` and marks the order shipped.

## Decisions

- **Core WooCommerce only.** WooCommerce has no built-in tracking field, and
  the tracking plugins each store tracking differently. A note plus the
  `completed` status works on every store. Plugin-specific tracking is not
  supported, and this is stated here.
- **Status first, then the note.** Setting the status is idempotent, so a
  retry after a failed note posts one note, not two. If the note fails, no
  `Shipment` is recorded, and the merchant sees the error and can retry.
- **Same tracking number twice:** the existing shipment is returned and
  nothing is sent.
- **WooCommerce's own email.** WooCommerce sends its "order completed" email
  when the status changes. That is the store's setting, and DropPilot does
  not control it.
- **One form for two channels.** The frontend form is shared with Shopify
  (`TrackingShipOrderForm`). Shopify's ids, text and tests are unchanged.

## Verified

`tests/integration/test_woocommerce_ship.py` (real Postgres) covers:

- completing the order and adding a customer note with the carrier, number
  and link;
- the same tracking number being sent once;
- a cancelled order being refused before any call;
- a store outage recording no shipment;
- the admin-only role check;
- a cross-tenant request getting 404.

Playwright (`ebay-orders.spec.ts`) checks the WooCommerce form's request
body, and that the Shopify and eBay forms are absent.
