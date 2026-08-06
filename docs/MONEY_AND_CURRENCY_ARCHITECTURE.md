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

| Role | Source (priority) |
|---|---|
| Supplier price currency | AliExpress field paired with the chosen amount |
| Destination selling currency | 1) Shopify store `currency` 2) tenant `default_currency` 3) unanimous supplier currency 4) product legacy (last resort) |

The Pricing tab badge shows **selling currency**, never an unrelated supplier code.

## Conversion types

| Type | Meaning |
|---|---|
| `direct` | Supplier currency already equals selling currency — no FX |
| `fx` | Real quote applied; rate + timestamp + provider recorded |
| `unavailable` | Conversion required but no valid quote — **block calculations** |

Identity `1.0` rates across different currencies are prohibited.
