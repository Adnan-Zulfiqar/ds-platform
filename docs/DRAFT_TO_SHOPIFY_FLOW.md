# Draft → Shopify flow

## Intended path

1. **Import as Draft** (AliExpress) → product in Drafts inbox.
2. Edit content / media / variants / pricing in `/drafts/{id}`.
3. **Publish to Store** → supplier refresh → readiness → Shopify payload →
   idempotent background job → `StoreListing` synced → product appears under
   Products.

## Current implementation (honest)

| Step | Status |
|---|---|
| Import as Draft | Live path exists (prior phases) |
| Drafts list + premium editor | On `cursor/premium-product-editor` |
| Media / variants / pricing / shipping | MVP shipped |
| SEO title/description/tags on Shopify payload | Shipped (no meta keywords) |
| Publish returns storefront/admin URLs | Shipped when REST signals allow |
| View in Store / Manage in Shopify | Shipped (verified URLs only) |
| `write_publications` Online Store channel | **Not implemented** (M24) |
| Live AliExpress → Shopify E2E this session | **Not verified** |
| Retry without duplicate listing | Deterministic handle adopt (A-04); re-verify live |

Do not label remaining publish gaps as M17 unless that debt’s definition is
updated. Prefer a product-publishing debt id when OAuth is connected but
draft→store E2E is still unproven.
