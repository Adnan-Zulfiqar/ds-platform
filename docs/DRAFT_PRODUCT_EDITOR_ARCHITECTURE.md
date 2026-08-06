# Draft Product Editor — architecture

**Status:** Stages 0–4 foundation (MVP). Not the full AutoDS-style workspace.

## Layering

```
drafts API → ProductService → Product*Repository → models
```

Draft routes are thin wrappers. Writes reuse Claude’s `ProductService` /
sanitization / supplier-twin pattern. No second product system.

## Publication projection

| Surface | Query |
|---|---|
| Drafts | Product with **no** `store_listings.status = synced` |
| Products | Product with **≥1** synced listing |

`Product.status = draft` alone does **not** mean “in Drafts” after a successful
publish (listing is the source of truth).

## Stage 4 data model (migration `0016`)

| Table | Merchant fields |
|---|---|
| `product_images` | `alt_text`, `is_supplier` |
| `product_variants` | `merchant_sku`, `sell_price`, `compare_at_price`, `is_enabled` |

Supplier sync must not silently undo merchant image order, featured image,
uploaded (`is_supplier=false`) images, removed supplier images, alt text, or
variant sell/merchant fields.

## Shopify publish mapping

- Disabled variants (`is_enabled=false`) are skipped.
- Selling price prefers `sell_price`, then `list_price`.
- SKU prefers `merchant_sku` when set; supplier identifiers stay separate.

## Not yet in architecture

Full pricing engine, inventory/shipping workspace, AI proposal accept flow,
readiness 0–100, version compare/restore UI, bulk undo sets.
