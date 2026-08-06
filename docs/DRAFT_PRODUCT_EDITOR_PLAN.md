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
| 0 | Audit + Drafts vs Products query split | Done (cherry-pick) |
| 1 | Clickable draft rows + Edit / Publish actions | Done |
| 2 | `PATCH/GET /drafts/{id}`, refresh, versions | Done (thin wrappers) |
| 3 | Editor shell + Overview / Description / SEO / Publish panel | Done (MVP) |
| 4 | Media + structured variants | Pending |
| 5 | Pricing / inventory / shipping | Pending |
| 6 | AI Studio proposals + full readiness | Pending |
| 7 | Idempotent publish UX polish | Pending (basic Publish to Store wired) |
| 8 | Bulk tools + live E2E verification | Pending |

## Terminology

- **Import as Draft** — supplier ingestion into DropPilot
- **Publish to Store** — push prepared draft to Shopify

## Verified locally this session

- Backend publication unit/integration tests (Stage 0) already green on prior branch
- Frontend lint/typecheck/build pending for this editor commit
- Live AliExpress → Shopify publish not claimed until M17 consent + manual run
