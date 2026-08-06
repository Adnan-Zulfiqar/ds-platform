# Draft Product Editor — plan

**Branch:** `cursor/draft-product-editor`
**Worktree:** `../droppilot-draft-editor`
**Date:** 2026-08-06

## Why drafts appeared under Products

`GET /products` listed every non-deleted tenant product. AliExpress import
always creates `Product.status = draft`, and Shopify publish writes
`StoreListing` without flipping that status. The Products UI therefore showed
unpublished imports.

Stage 0 (cherry-picked from `cursor/product-workspace-v2`) fixes this:

| Surface | Condition |
|---|---|
| Drafts | No synced `StoreListing` |
| Products | At least one synced `StoreListing` |

## Reused Claude Product Editor work

Do not recreate:

- Description import + `sanitize_html` + `supplier_description` (`10141f5`)
- `PATCH /products/{id}` + `ProductService` + supplier title/brand twins (`b657363`)
- Identity-preserving variant/image sync (`e322a8d`)

Draft routes delegate writes to the same `ProductService` / import sync path.

## Stage progress

| Stage | Scope | Status |
|---|---|---|
| 0 | Audit + Drafts vs Products query split | Done — merged to `develop` (`da83d52`) |
| 1 | Clickable draft rows + Edit / Publish actions | Done — on `develop` |
| 2 | `PATCH/GET /drafts/{id}`, refresh, versions | Done — on `develop` |
| 3 | Editor shell + Overview / Description / SEO / Publish panel | Done (MVP) — on `develop` |
| 4 | Media + structured variants | **Done (MVP)** — migration `0016`; reorder/featured/alt/add-URL/remove; variant sell price / merchant SKU / enable; sync preserves merchant fields |
| 5 | Pricing / inventory / shipping | **Done (MVP)** — Decimal pricing workspace API; inventory freshness labels; shipping package fields (`0017`) |
| 6 | AI Studio proposals + full readiness | Pending (basic readiness sidebar exists) |
| 7 | Idempotent publish UX polish | Partial — Publish panel calls existing Shopify publish |
| 8 | Bulk tools + live E2E verification | Pending |

### Stage 4 limitations (honest)

- No S3 binary upload / crop / compress yet (add-by-URL only).
- Option axes are parsed from flattened `label` for display; renaming individual
  option keys as a matrix editor is not built.
- Barcode and weight/dimensions columns not added.

### Import destination (2026-08)

AliExpress availability is destination-specific. Import as Draft records the
ship-to country used (`import_ship_to_country` / checked-at). The Shipping tab
shows **Imported for** and **Last checked**. Changing destination refreshes the
supplier snapshot without creating a second Product row and without overwriting
merchant-edited title/description/images/sell prices (existing `_upsert`
rules). See `docs/ALIEXPRESS_INTEGRATION.md` and technical debt M24/M25.

### Stage 5 limitations (honest)

- FX conversion is identity (no live rate feed).
- Freight quotes usually absent from product detail — UI shows “Shipping cost unavailable”.
- No inventory buffer / reserved / Shopify-on-hand columns yet.
- Catalogue `PricingEngine` product-level apply still separate; draft tab uses variant apply.

## Terminology

- **Import as Draft** — supplier ingestion into DropPilot
- **Publish to Store** — push prepared draft to Shopify

## Verified this session

| Check | Result |
|---|---|
| Stages 0–3 merge to `develop` (`da83d52`) | Done + pushed |
| Focused backend tests (draft editor + media/variants + workspace) | **10 passed** |
| Frontend lint / typecheck / build (Stage 4) | Passed (standalone symlink EPERM warning only) |
| Live Drafts/Products UI with real imported product | Not re-verified in this commit |
| Live Shopify publish | Not verified — track separately from OAuth debt |

## Remaining (Stages 5–8)

Pricing engine, inventory/shipping workspace, AI proposal workflow, full
readiness/history, idempotent publish UX, bulk tools, Playwright + live
AliExpress → Shopify E2E. No completion tag until those gates pass.
