# Money and Currency Architecture

**Status:** Active (financial integrity)  
**Related:** [PRICING_CURRENCY_DEFECT_AUDIT.md](PRICING_CURRENCY_DEFECT_AUDIT.md),
[FX_RATE_PROVIDER.md](FX_RATE_PROVIDER.md)

## Money

`app.domain.money.Money` binds a `Decimal` amount to an ISO 4217 currency code.

Rules:

- `Money(23.74, USD) ≠ Money(23.74, GBP)`
- Addition / subtraction require matching currencies (`CurrencyMismatchError`)
- Cross-currency work requires an explicit `CurrencyConversion` via `FxService`
- Formatting is presentation-only (`formatMoney` on the frontend)
- Binary floats are forbidden for authoritative amounts

## Currency authority

| Role | Source |
|---|---|
| Supplier price currency | AliExpress / product source field paired with the amount |
| Shopify selling currency | **Only** `Store.currency` when `currency_last_synced_at` is set (from GraphQL `shop.currencyCode`). Unsynced Shopify currency is **unavailable** — no tenant / supplier / USD fallback. |
| Non-Shopify / channel-independent | Explicit store currency or tenant `default_currency` when configured |

`supplier_currency ≠ selling_currency` conceptually. Never relabel a CNY amount as GBP.

The Pricing tab badge shows **selling currency**, never an unrelated supplier code.

Authority pipeline for Shopify pricing:

```
supplier Money → verified Shopify selling currency → FxService → converted cost → pricing engine
```

Out of scope here: VAT, payment fees, Shopify fees, landed-cost strategy (M24C).


## Conversion types

| Type | Meaning |
|---|---|
| `direct` | Supplier currency already equals selling currency — no FX |
| `fx` | Real quote applied; rate + timestamp + provider recorded |
| `unavailable` | Conversion required but no valid quote — **block calculations** |

Identity `1.0` rates across different currencies are prohibited.
