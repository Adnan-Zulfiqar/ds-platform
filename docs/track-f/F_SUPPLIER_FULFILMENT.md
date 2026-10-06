# Track F — supplier auto-ordering and tracking sync

Status: **decided 2026-10-05 (D-017).** Built in three PRs:

| Stage | Scope | State |
|---|---|---|
| F1 | Groundwork: Shopify order lines with the exact variant (migration 0049); the AliExpress refresh no longer queries channel orders | PR #87 |
| F2 | `supplier_orders` + `fulfilment_settings` (migration 0050); review, the "Place on AliExpress" action, placement task | PR #88 |
| F3 | Automatic mode on import; tracking sync back to the store; manual "send tracking" | Merged (#89) |
| F4 | Screens: order panel, settings switches | This PR |

## What the owner decided (D-017)

- **Button first, auto switch.** Every channel order can be placed with one
  action. A per-workspace *Auto-order* switch, **off by default**, places
  paid orders automatically once the owner trusts it.
- **Shipping: the supplier's default.** No method is sent unless the owner
  sets a fallback in the settings.
- **Tracking: notify the buyer.** Shopify and WooCommerce send their normal
  shipping email; eBay always notifies.
- **One probe allowed**, and run on 2026-10-05: an empty order request,
  which cannot create an order.

## What is known, and what is not

| Fact | Evidence |
|---|---|
| `aliexpress.ds.order.create` exists and the connected account may call it | The empty probe answered `MissingParameter: logistics_address` (a parameter check, not a permission refusal) |
| `aliexpress.trade.ds.order.create` is not the method | `InvalidApiPath` |
| `aliexpress.ds.order.tracking.get` is reachable | Normal envelope returned |
| A created order is **unpaid**; the merchant pays it on AliExpress | AliExpress dropshipping documentation (Ali2Woo knowledge base) |
| **The success body of either method** | **Never seen.** No real order has been placed |

Because the success bodies are unknown, the parsers in
`integrations/aliexpress/ordering.py` search the answer for the documented
fields (`is_success` + `order_list`; `mail_no` / `logistics_no`) instead of
trusting one path, and **anything short of an explicit success with order
ids is a failure**, never a placed order.

## How placing works (F2)

1. **Review.** An order is placeable only when:
   - it is a Shopify, eBay or WooCommerce order, paid, not cancelled;
   - the address has name, phone, address line, city and country;
   - it is not going to Brazil or Chile (AliExpress needs a tax id there,
     which DropPilot does not store);
   - every line maps to **exactly one** AliExpress SKU: the line's variant
     (Shopify gives it since F1), or the product's only variant. A
     multi-variant product whose sold variant is unknown (eBay and
     WooCommerce lines link only to the product) is a review reason, not a
     guess.

   Otherwise the row is `needs_review` with stable reason codes.
2. **Queue.** `POST /orders/{id}/supplier-order` (admin, billing-gated)
   records `queued` and enqueues `supplier_orders.place` after commit.
3. **Place**, in three transactions: `queued → placing` is committed
   *before* AliExpress is called; then the call; then `placed` (with the
   AliExpress order ids) or `failed` (with AliExpress's code and message).

**What prevents a double order.** The task never retries
(`max_retries=0`), and only `queued` rows are placed. A crash between the
call and recording its answer leaves `placing`, which nothing retries: the
merchant checks AliExpress first. A second request on a `queued`,
`placing`, `placed` or `shipped` row is a 409. `out_order_id` carries
DropPilot's order id; whether AliExpress also de-duplicates on it is not
documented.

## How tracking works (F3)

Every three hours `supplier_orders.sync_tracking_all` fans out to the
workspaces with a connected AliExpress account (the set the order sync
already sweeps, so no new unscoped lookup). For each placed order it asks
`aliexpress.ds.order.tracking.get`, stores the number and carrier, and, with
*Auto-tracking* on, sends it to the store through the same "mark shipped"
code the manual forms use (idempotent on the tracking number), with buyer
notification. A store refusal is recorded on the row and the number kept.

**One parcel only.** An order split across several AliExpress sellers
produces several parcels; the channel "mark shipped" calls take one number
for the whole order. Such orders keep their numbers on screen and are never
pushed automatically (`multiple_parcels`).

## Known limitations

- Payment stays manual on AliExpress (by API design, not by choice).
- eBay validates the carrier against its own list; an AliExpress carrier
  name eBay does not know is refused, recorded, and shipped by hand.
- eBay and WooCommerce publish one SKU per product, so a multi-variant
  product sold there needs review before ordering.
- Brazil and Chile orders are placed by hand (tax id).
- No live order has been placed. The first real one is the owner's,
  after which the parsers should be checked against the real bodies.

## Data protection

Placing sends the buyer's name, phone and shipping address to AliExpress,
a new recipient, recorded in the processing register (§10f). The
`supplier_orders` row itself holds no address: only product ids, SKU
strings, quantities and AliExpress order ids.
