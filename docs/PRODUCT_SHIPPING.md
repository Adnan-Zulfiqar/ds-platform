# Product shipping workspace

## A. Supplier shipping

Read-only snapshot from AliExpress package/logistics (weight, dims, delivery
days, ship-to, warehouse). Freight often unavailable — shown explicitly.

## B. Product shipping data (merchant editable)

- requires_shipping
- weight / dimensions / units
- country of origin, HS code, customs description, handling time

## C. Shopify customer shipping

Delivery profiles/zones/rates need `read_shipping` / `write_shipping` and
explicit merchant confirmation. **Not implemented** in this release (least
privilege). Product publishing continues without those scopes.

## Scopes

Current default scopes do **not** include shipping profile management. See
`.env.example` comments and `TECHNICAL_DEBT.md` M24.
