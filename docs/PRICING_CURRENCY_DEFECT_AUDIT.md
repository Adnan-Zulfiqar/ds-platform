# Pricing Currency Defect Audit

**Branch:** `cursor/pricing-currency-integrity`  
**Base:** `origin/develop` @ `06b07b8`  
**Status:** Diagnosis complete — implementation follows this document.  
**Severity:** Financial integrity (publish-blocking)

---

## Observed defect

On the draft Pricing tab:

| Field | Displayed |
|---|---|
| Page badge | CNY |
| Supplier cost | USD 23.7400 |
| Proposed / profit / break-even | CNY amounts equal to the USD numeric values |

`fxNote` states identity conversion; `conversionRateTimestamp` is always `null`.

An amount cannot change currency by relabeling. Treating USD 23.74 as CNY 23.74 is invalid arithmetic.

---

## Root cause (three stacked defects)

### 1. Identity FX is intentional and unguarded

`PricingEngine.convert_currency` ignores both currency arguments and returns the
input amount. `conversion_rate_timestamp` is hard-coded `None` in `_variant_row`.

```python
# backend/app/services/pricing_engine.py
def convert_currency(amount, *, from_currency, to_currency) -> Decimal:
    _ = from_currency, to_currency
    return amount
```

Unit test `test_currency_hook_is_identity` **locks this in** (USD→EUR unchanged).

### 2. Dual currency labels on one calculation row

| Column | Currency source |
|---|---|
| Supplier cost | `variant.currency` (`supplierCurrency`) |
| Badge, sell, proposed, profit, break-even | `product.currency` (`workspace.currency`) |

Profit math:

```text
profit = sell − converted_supplier − shipping − fees
```

with **no** requirement that currencies match. Draft conversion even calls
`convert_currency(supplier → supplier)`, never targeting workspace currency.

### 3. Import request currency ≠ persisted currency

AliExpress is called with `target_currency`, but `products.currency` /
`variants.currency` store **response** `currency_code` fields. Product base and
SKU codes can diverge (e.g. base CNY, SKU USD). Shopify shop currency and
tenant `default_currency` are **not** used by the Pricing tab.

---

## End-to-end value path

```text
AliExpress ds.product.get (target_currency, ship_to_country)
  → mapper.map_product / map_variants
      products.currency  ← ItemBaseInfo.currency_code   (badge)
      variants.currency  ← Sku.currency_code            (supplier label)
      variants.cost_price ← Sku.offer_sale_price / sku_price
  → PricingEngine.draft_workspace
      convert_currency identity, timestamp=null
      profit in unlabeled Decimal
  → DraftPricingWorkspaceRead.currency = product.currency
  → frontend draft-pricing-panel
      badge/proposed/profit ← workspace.currency
      supplier ← row.supplierCurrency
```

---

## Why each symptom appears

| Symptom | Cause |
|---|---|
| USD beside CNY calculations | Supplier column uses variant currency; calculated columns use product currency |
| `conversionRateTimestamp` null | Hard-coded; no FX provider |
| Identity across different codes | `convert_currency` discards currencies; tests encode this |
| Badge shows CNY | Badge = `product.currency` from AE base, not Shopify store / tenant |

---

## What is *not* the cause

- Frontend inventing FX (display only formats server Decimals)
- Shopify OAuth or AliExpress OAuth
- Floating-point (amounts are `Decimal` / `Numeric(16,4)`)

---

## Authority model required (fix direction)

**Supplier price currency:** AliExpress field paired with the chosen amount.

**Destination selling currency (priority):**

1. Selected Shopify store `shop.currencyCode` (persist on connection refresh)
2. Store-level override when supported
3. Workspace / tenant base currency
4. Explicit merchant selection when no store is selected

**Never** use CNY solely because a supplier or legacy product field used CNY.

---

## Key files

| Path | Role |
|---|---|
| `backend/app/services/pricing_engine.py` | Identity FX, draft workspace, profit math |
| `backend/app/schemas/draft_pricing.py` | Wire shapes |
| `backend/app/integrations/aliexpress/mapper.py` | Dual currency persistence |
| `backend/app/services/product_import.py` | `target_currency` request |
| `frontend/components/drafts/draft-pricing-panel.tsx` | Dual labels |
| `backend/tests/unit/test_pricing_engine.py` | Encodes identity as correct |
| `docs/TECHNICAL_DEBT.md` (M23) | Known deferred FX |

---

## Classification of existing data

| Class | Criteria | Action |
|---|---|---|
| Valid | Supplier currency == selling currency; rates N/A | Keep |
| Safely repairable | Same ISO code, formatting only | Keep |
| Needs recalculation | Cross-currency with identity conversion / null timestamp | Mark `needs_recalculation`; block publish |
| Invalid / untrusted | Mixed labels with no conversion evidence | Retain raw evidence; require new preview |

Do **not** silently rewrite historical financial rows.

---

## Acceptance gates for “fixed”

- No mixed-currency arithmetic without explicit `CurrencyConversion`
- No 1:1 fallback when currencies differ
- Calculations blocked with actionable message when FX required and unavailable
- Page badge shows destination selling currency
- Supplier amounts keep their true source currency
- Calculated columns use only selling currency after a valid conversion or direct target price
- Real FX rate + timestamp when conversion occurs
- Publish blocked when conversion unresolved

---

## Live product under investigation

Title: *1pair Elasticity Shock Absorption Breathable Running Orthopedic Insoles*  
AliExpress ID: `1005006966423396`  
UUID (local): `dd8afabb-89bd-479b-a22a-e6101271b80c`  

Post-fix acceptance must re-request with GB + GBP and record returned price fields
(see live acceptance checklist in the phase prompt).
