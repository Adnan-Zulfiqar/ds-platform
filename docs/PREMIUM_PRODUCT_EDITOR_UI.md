# Premium Product Editor UI

**Branch tip (header work):** `feat/ui-draft-editor-header`  
**Header commit lineage:** `6671589` + follow-up polish  
**Base ancestry:** `cursor/premium-product-editor` → Stages 0–5 draft editor

## Desktop hierarchy (three sticky layers)

1. **Context** — breadcrumb `Products / Drafts / Edit Product` · save-state indicator · inspector toggle  
2. **Identity + actions** — thumbnail (64–72px) · title · AliExpress ID · supplier · external link · status badges · supplier sync · readiness/SEO · margin/Shopify chips · **Preview** · **Save Draft** (secondary) · **Publish to Store** (primary) · **More**  
3. **Tabs** — Overview → History with underline/tint active state and optional issue/score suffixes

## Tablet

Identity stays full width; action cluster wraps under identity above tabs. Publish remains visible. Tabs scroll horizontally with a fade hint.

## Mobile

Compact header: back · identity · badges · More. Sticky bottom bar: Save · Preview · Publish (safe-area padding). Desktop action clusters are not rendered.

## Action hierarchy

| Priority | Action |
|---|---|
| Primary | Publish to Store (or View in Store / Push Updates / Fix N issues) |
| Secondary | Preview, Save Draft |
| Tertiary (More) | Refresh Supplier Data, Optimize with AI, Open AliExpress Listing, View History, Open History tab |

## Preview behaviour

**Draft Preview** opens a right-hand sheet (`DraftPreviewPanel`) — a DropPilot storefront representation of the current draft (desktop/mobile frames, SEO snippet toggle). It is **not** a live Shopify storefront and does **not** merely switch editor tabs.

## Unsupported More-menu actions

Duplicate Draft, Archive Draft, and Delete Draft are **hidden** until APIs exist. Disabled no-op rows are not shown.

## Accessibility (smoke)

- Breadcrumb `nav` labelled “Breadcrumb”
- Tabs use `role="tablist"` / `role="tab"` with arrow-key navigation
- More has `aria-label="More actions"`; Escape closes and returns focus (Playwright)
- Status badges include readable text
- Thumbnail uses product-title alt text
- Publish disabled state exposes a reason via tooltip when blocked by readiness issues

## Remaining limitations

- Duplicate / Archive / Delete not implemented
- Draft Preview is local-only (not Shopify storefront)
- Full WCAG audit not claimed from smoke tests
- Autosave still re-renders the editor shell when title state changes (header receives `title` overlay from local state)
