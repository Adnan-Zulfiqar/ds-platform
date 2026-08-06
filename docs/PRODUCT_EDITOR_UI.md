# Product Editor UI

## Surfaces

| Route | Purpose |
|---|---|
| `/drafts` | Unpublished imports (no synced listing) |
| `/products` | Catalogue with ≥1 synced Shopify listing |
| `/drafts/[productId]` | Draft editor workspace |
| `/imports/history` | Import job history |

## Premium header

See [PREMIUM_PRODUCT_EDITOR_UI.md](PREMIUM_PRODUCT_EDITOR_UI.md) for the
three-layer sticky header, action hierarchy, Draft Preview sheet, More menu
rules, and responsive behaviour.

## Editor tabs

| Tab | Capability |
|---|---|
| Overview | Title, brand, category, tags, vendor |
| Description | HTML description (sanitized on save) |
| Media | Reorder, featured, alt text, add URL, remove |
| Variants | Label, merchant SKU, sell/compare-at, enable |
| Pricing | Draft pricing workspace |
| Inventory | Stock freshness labels |
| Shipping | Package / logistics / customs |
| SEO | Expert SEO workspace + advisory score |
| AI Studio | Placeholder (Optimize with AI via More) |
| Publishing | Store picker + Publish to Store |
| History | Placeholder (AI version sheet via More) |

## Design notes

Reuse DropPilot shell (nav badges, cards where interactive). Do not copy
AutoDS branding.
