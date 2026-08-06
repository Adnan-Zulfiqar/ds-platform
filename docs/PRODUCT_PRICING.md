# Product pricing (draft workspace)

Authoritative math is Decimal on the backend (`PricingEngine.draft_workspace`).

## Per-variant fields

Supplier cost, currency, converted cost (identity FX today), shipping when known,
handling, fee estimate, sell/compare-at, profit, margin, break-even, rule source,
manual override.

## Apply modes

- percentage markup
- fixed markup
- target margin
- set sell price
- set compare-at

Optional: min profit, min/max sell, psychological rounding, include shipping in
landed cost when freight is known.

Missing freight → warning; never treated as $0 for publish decisions.
