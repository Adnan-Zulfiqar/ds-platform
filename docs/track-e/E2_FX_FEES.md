# Track E2 — FX M24B / M24C

Status: **M24C sale fees implemented and unit-verified; M24B's currency work
was already done; the AliExpress freight quote (M27) stays blocked on a real
API contract.** Authorised by the owner (D-012).

## What the audit found

| Item | Before this track |
|---|---|
| M24B — AliExpress ship-to / currency derivation | **Done earlier** (`docs/ALIEXPRESS_LOCALIZED_PRICING.md`, migration `0022`); the roadmap row was stale |
| M24C — landed cost: item + supplier shipping + duty % + fixed fees | **Done earlier** (`landed_cost`, global pricing rules `0023`) |
| M24C — fees that grow with the price (marketplace final-value, payment) | **Missing** |
| M27 — AliExpress freight quote (shipping cost per service) | **Missing**, and `ds.product.get` returns no shipping cost at all (`TECHNICAL_DEBT.md` M27) |

## Built: sale fee as a share of the selling price

`pricing_rules.sale_fee_percent` (migration `0042`, nullable — every existing
rule prices exactly as before). The price is grossed up so the merchant's
intent survives the fee:

| Strategy | Formula with fee *f* |
|---|---|
| Percentage / fixed / hybrid / tiered markup | strategy price ÷ (1 − *f*) — the markup is intact after the fee |
| Target margin *m* | cost ÷ (1 − *m* − *f*) — the margin is *after* the fee; *m* + *f* < 100% |
| Minimum profit / per-variant floor | (cost + floor) ÷ (1 − *f*) — the floor is net |

Reported profit and margin are net of the fee (`PriceCalculation.sale_fee`).
Global Rules → pricing rule form gains "Sale fee (% of selling price)", with
the same bounds checked in the browser and on the server.

The draft Pricing tab already had a per-request fee estimate; it is unchanged
and independent of the rule's fee (documented, not merged — merging would
change what a merchant typed into that tab means).

## Not built, and why

**M27 — AliExpress freight quote.** The dropshipping API's product call
returns no shipping cost; a separate freight-quote method exists, but its
request and response could not be confirmed without a live AliExpress
session. Building a shipping-service picker on an unverified contract would
be inventing fields. **Owner step:** with AliExpress connected, the agent can
capture one real freight response and build against it.

## Verification

Unit: 7 new tests (markup survives the fee, margin is net, floor is net,
profit/margin reporting, bounds) plus the existing pricing suite (53 passed).
