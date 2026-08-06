# Premium Product Editor UI

**Branch:** `cursor/premium-product-editor`  
**Base:** `origin/develop` @ Stages 0–5 draft editor

## Layout

- Sticky action header: back, thumbnail, editable title, status, sync/save
  indicators, Refresh, Preview, Optimize with AI, Save Draft, Publish
- Sticky tab navigation
- Main editor + collapsible right inspector (readiness, SEO score, listing)
- Debounced autosave (~1.8s) with manual Save and Ctrl/Cmd+S
- Ctrl/Cmd+P jumps to Publishing
- Unsaved navigation warning via `beforeunload`

## Tabs

Product, Description, Media, Variants, Pricing, Inventory, Shipping, SEO,
AI Studio (placeholder), Publishing, History (placeholder)

## Design

DropPilot shell only — no AutoDS branding/assets. Light/dark via existing theme.
