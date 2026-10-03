# Track E7 W4a — import WooCommerce orders

Status: **on-demand import implemented; webhooks (W4b) not built.**
Verified against a faked WooCommerce API only.

## What it does

Settings → Integrations → WooCommerce → **Import recent orders** (owner or
admin) calls `POST /integrations/woocommerce/stores/{id}/orders/import`.
The endpoint takes `?days=1..30` and defaults to 7. It reads
`GET /orders?modified_after=…&dates_are_gmt=true`, 50 per page and at most
10 pages, and upserts into the shared `orders` and `order_items` tables.

| WooCommerce status | Fulfilment | Payment |
|---|---|---|
| pending, on-hold, failed | awaiting payment | unpaid |
| processing | paid | paid |
| completed | shipped | paid |
| cancelled | cancelled | unknown |
| refunded | refunded | refunded |
| checkout-draft, custom | *skipped* | — |

- **Fields imported:** the recipient and shipping address (falling back to
  billing for name, phone and country), currency, total, shipping total, and
  the created and paid times.
- **Line items** are linked to their draft through the W2 SKU `dp-<id>`.
  Each line's unit price is the line total divided by the quantity.

## Decisions

- **Namespaced external id.** WooCommerce order ids are small integers per
  site. Two stores in one workspace would collide on
  `uq_orders_tenant_source_external`, so the stored id is
  `<store id>:<order id>`.
- **Enum migration `0045`.** It adds `'woocommerce'` to `order_source`.
  Downgrade is a no-op, as in 0008 and 0040, because PostgreSQL cannot drop
  an enum value without rebuilding the type.
- **No scheduled import.** A periodic import across workspaces needs the
  same approval as the W3 sweep (B-016). Webhooks (W4b) will give near-live
  orders without one.

## Not built

- **W4b webhooks.** Delivery is unauthenticated, so the endpoint must find
  the store without a session. The plan is the D-013 pattern: the tenant and
  store are named in the delivery URL and the request is authorised by the
  per-store HMAC secret. That needs a public API base URL configured for
  delivery.
- **W5, marking shipped** (an order note plus status `completed`).

## Verified

`tests/integration/test_woocommerce_orders.py` (real Postgres) covers:

- the import with address, mapped status, totals and an SKU-linked item;
- a re-import updating rather than duplicating, and drafts being skipped;
- two stores with the same order number staying separate;
- paging stopping on a short page;
- the role check and cross-tenant 404s.

Playwright (`woocommerce-connect.spec.ts`) covers the import button.
