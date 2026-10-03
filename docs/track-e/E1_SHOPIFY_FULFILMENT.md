# Track E1 — Shopify fulfilment push

Status: **implemented; verified against a faked Shopify GraphQL client only.**
Authorised by the owner on 2026-10-03 (D-012, Track E in roadmap order).

## Plan → what was built

| Need | Built |
|---|---|
| Tell Shopify an order shipped, with tracking, so the customer is notified | `POST /api/v1/integrations/shopify/orders/{id}/fulfilments` (admin): finds the order's **open** fulfillment orders (`OPEN`, `IN_PROGRESS`) and calls `fulfillmentCreate` with carrier, tracking number, optional https tracking link and the notify choice |
| Never tell Shopify twice | Order row locked; the same tracking number returns the existing shipment and calls nothing |
| Clear failures | Shopify `userErrors` → `shopify_fulfilment_rejected` with Shopify's words; missing scope (`ACCESS_DENIED`) → `shopify_fulfilment_scope_missing` ("reconnect"); nothing open → plain refusal before any mutation |
| UI | "Mark shipped on Shopify" on Shopify order pages (owner/admin): carrier (Shopify names suggested), tracking number, optional link, notify checkbox |

## Decisions

- **Manual, not automatic.** Supplier tracking lands on the AliExpress order
  row; nothing links it to the customer's Shopify order. Linking would be a
  guess, so the merchant makes the link by entering the tracking number.
  Automating needs a supplier-order ↔ sales-order relation (future work).
- **New scopes** `read_/write_merchant_managed_fulfillment_orders` in the
  default `SHOPIFY_SCOPES`. **Stores connected before this change must
  reconnect** to grant them; until then the push explains exactly that.
- **GraphQL, not REST**: Shopify's REST fulfilment endpoints are legacy.

## Not verified

No call to a real Shopify store. Owner step: reconnect the test store (new
scopes), import an order, mark it shipped, confirm the customer email and the
order's fulfilment status in Shopify admin.
