# AliExpress localized pricing (M24B)

Real, live-traced mapping of `aliexpress.ds.product.get`'s currency
behaviour, and the fix for the one real bug it exposed. Scope: currency
derivation and request correctness only. Shipping quotes, tax handling,
landed cost, and the Pricing tab redesign are explicitly **not** part of this
pass — see [Remaining scope](#remaining-scope-not-in-this-pass).

## The live contract

Traced against a real product (`1005009972613417`), requesting
`ship_to_country=GB`, `target_currency=GBP`, `target_language=en` through the
already-connected AliExpress OAuth credentials from this session's live
verification pass. This is the first time this endpoint's currency behaviour
was confirmed against the real gateway rather than documentation — see
`docs/ALIEXPRESS_INTEGRATION.md`'s "Known limitations" for the general
verification status.

**Request** — unchanged, already correct before this pass
(`ProductImportService._fetch_product`):

```
product_id=1005009972613417
ship_to_country=GB
target_currency=GBP
target_language=en
```

**Response, the part that matters:**

| Field | Value | Localized to `target_currency`? |
|---|---|---|
| `ae_item_base_info_dto.currency_code` | `CNY` | **No** — always the seller's native listing currency |
| `ae_item_sku_info_dtos[].currency_code` | `GBP` | **Yes** — every one of the 6 real SKUs reported the requested target |
| `ae_item_sku_info_dtos[].sku_price` | e.g. `4.92` | List/original price |
| `ae_item_sku_info_dtos[].offer_sale_price` | e.g. `2.31` | Current sale price — noticeably lower than `sku_price` |
| `ae_item_sku_info_dtos[].offer_bulk_sale_price` | e.g. `2.31` | Matched `offer_sale_price` on every SKU observed; presumably diverges at higher quantity tiers |
| `ae_item_sku_info_dtos[].price_include_tax` | `true` | A real boolean, present on every SKU |
| `logistics_info_dto` | `{delivery_time, ship_to_country}` only | **No shipping cost, service list, or currency at all** |

**Conclusions, live-verified, not assumed:**

- `target_currency` **is honoured**, but only at SKU level. The product-level
  `currency_code` is not a bug to route around — it is the seller's actual
  native currency, and stays useful as audit information once treated as
  such rather than conflated with the localized SKU prices next to it.
- The already-existing price-field priority (`catalog.py`'s
  `sale_price` property: `offer_sale_price or sku_price`) was **already
  correct** — it already prefers the current sale price over the list price.
  Nothing needed to change there.
- `ds.product.get` carries **no shipping cost or service data** at all — only
  `delivery_time` (a day count) and an echo of the requested
  `ship_to_country`. A shipping/freight quote needs a different AliExpress
  API method, not yet identified or integrated anywhere in this codebase.
  Section 11 of the original request (shipping service selection) is not
  buildable without that separate investigation.
- No tax/VAT field exists anywhere in the response except the per-SKU
  `price_include_tax` boolean. There is no explicit tax rate or amount to
  read.

## The actual bug, and the fix

Not a missing request parameter — `target_currency` was already sent
correctly on the very first import. The real defect: `currency` had a
hardcoded `"USD"` default at both the schema layer
(`ProductImportRequest.currency`) and the service layer
(`ProductImportService.import_product`'s `currency: str = "USD"`), and every
refresh/sync call site (`POST /products/{id}/sync`,
`POST /drafts/{id}/refresh`, the scheduled resync task) omitted the field
entirely. A GB-destined product would correctly request `ship_to_country=GB`
on every refresh, but silently request `target_currency=USD` at the same
time — a mismatched pair, not a hypothetical one.

**Fix:** `currency` is now `str | None = None` at both layers (mirroring how
`ship_to_country` already worked), and `ImportDestinationService` gained
`resolve_currency()` alongside its existing `resolve()` — same shape, same
"never invent a default silently" principle. See
`docs/ALIEXPRESS_INTEGRATION.md`'s "Product import destinations" section for
the exact priority order.

A second, related bug surfaced while live-tracing the fix:
`ProductImportService._upsert`'s `map_product()` was pairing
`cost_price_min`/`cost_price_max` (computed from SKU sale prices — see
`ProductDetail.price_range`) with `product.currency = base.currency_code` —
the *unlocalized* native currency, not the currency the min/max figures were
actually in. For this real product that meant `cost_price_min: 4.92` labelled
`CNY` when it was genuinely `GBP`. Fixed by deriving `Product.currency` from
the SKUs themselves (`mapper._skus_currency`, unanimous-currency check,
falling back to the native currency only when SKUs disagree or are absent —
the same "no data to be confident about" case
`PricingEngine._resolve_selling_currency` already treats as unresolved rather
than guessing).

`refresh_draft` (`POST /drafts/{id}/refresh`) had a third, smaller
inconsistency: unlike `sync_product`, it never explicitly reused the
product's own `import_ship_to_country`, relying entirely on
`ImportDestinationService.resolve()`'s own fallback chain. In production
(no platform-wide `DEFAULT_SHIP_TO_COUNTRY` configured) this converges on the
same answer via the "last successful destination" step — but it is an
indirect path for no reason, and diverges the moment a platform default *is*
configured (as the test environment does, for unrelated reasons — see
`tests/integration/conftest.py::_platform_import_defaults`). Fixed to match
`sync_product`'s explicit pattern.

## Data model (M24B, migration `0022`)

Two new nullable columns on `products`, no backfill (existing rows: neither
was knowable after the fact — a `NULL` means "imported before this
distinction existed," not "confirmed native" or "confirmed missing"):

- **`supplier_native_currency`** — the seller's own listing currency
  (`ae_item_base_info_dto.currency_code`), audit/display only. AliExpress
  never localizes this regardless of what was requested, so it must never be
  used for pricing math — `currency` is the field pricing code reads.
- **`import_currency`** — the `target_currency` actually requested on the
  last successful import/refresh, pairing with the pre-existing
  `import_ship_to_country`. Lets a later question ("was this draft's
  supplier price ever fetched in USD?") be answered from the stored row
  rather than re-derived from whatever the store/tenant currency happens to
  be *now*.

`ProductVariant.currency`/`cost_price` needed no new fields — they already
capture per-SKU currency and price independently (`mapper.map_variants`), so
once the request correctly asks for GBP/USD, those columns are correctly
localized without any schema change.

## Remaining scope, not in this pass

Deliberately out of scope, per an explicit scoping decision rather than an
oversight:

- **AliExpress shipping/freight quotes.** `ds.product.get` returns none —
  needs its own API investigation (method name, params, auth scope) before
  any shipping-service picker or landed-cost calculator can be built on real
  data instead of invented fields.
- **Supplier VAT/tax display beyond the raw `price_include_tax` boolean**,
  and all customer-facing tax-handling settings (UK VAT mode, US sales-tax
  mode).
- **Landed-cost calculation and markup-basis strategy** (item-only vs.
  item+shipping).
- **Price provenance UI** (`ALIEXPRESS_LOCALIZED` / `FX_CONVERTED_SOURCE` /
  `MANUAL_OVERRIDE` badge) and the Pricing tab redesign.
- **Playwright coverage** for any of the above.

These need their own scoped passes — seeing the real shipping/tax API
contract first is a prerequisite the same way seeing the real pricing
contract was for this one.
