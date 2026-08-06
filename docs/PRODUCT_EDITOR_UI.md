# Product Editor UI

## Surfaces

| Route | Purpose |
|---|---|
| `/drafts` | Unpublished imports (no synced listing) |
| `/products` | Catalogue with ≥1 synced Shopify listing |
| `/drafts/[productId]` | Draft editor workspace |
| `/imports/history` | Import job history |

## Editor tabs (Stage 4 MVP)

| Tab | Capability |
|---|---|
| Overview | Title, brand, category, tags, vendor |
| Description | HTML description (sanitized on save) |
| SEO | SEO title/description/keywords/slug |
| Media | Reorder, featured, alt text, add URL, remove |
| Variants | Label, merchant SKU, sell/compare-at, enable |
| Publish | Store picker + Publish to Store |

## Design notes

Reuse DropPilot shell (nav badges, cards where interactive). Do not copy
AutoDS branding. Premium polish (table/grid, sticky header, bulk tools) is
Stage 8 — not claimed complete.
