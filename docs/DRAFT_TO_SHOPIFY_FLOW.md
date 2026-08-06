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
| Drafts list + editor (Stages 0–3) | On `develop` |
| Media / variant merchant fields (Stage 4 MVP) | On `cursor/draft-product-editor` |
| Publish panel calling existing Shopify publish | Wired |
| Full readiness gate + payload preview | Partial / basic |
| Live AliExpress → Shopify E2E on this branch | **Not verified here** |
| Retry without duplicate listing | Designed in prior Shopify work; re-verify live |

Do not label remaining publish gaps as M17 unless that debt’s definition is
updated. Prefer a product-publishing debt id when OAuth is connected but
draft→store E2E is still unproven.
