# Product Workspace V2 — Stage 0 plan

**Branch:** `cursor/product-workspace-v2`
**Worktree:** `../droppilot-product-workspace`
**Base:** `origin/develop` @ `e322a8d` (variant/image sync identity — M20)
**Date:** 2026-08-06

Parallel-work check: Claude Product Editor Stages 1–2b are **already committed**
on develop (`10141f5`, `b657363`, `e322a8d`). Stashes
`wip: leftover product editor test` and
`wip: product editor (parked for Shopify live deploy)` remain in the main
repo and must not be popped or overwritten. Stage 0 does **not** recreate
PATCH product APIs, description sanitization, or identity-preserving sync.

---

## 1. Root cause — drafts on Products

| Layer | Current behaviour |
|---|---|
| Import | Always creates `Product.status = draft` (`ProductImportService` / model default). |
| List API | `GET /api/v1/products` → `ProductRepository.list(params)` with **no** status or publication filter. |
| Repository | `_base_query` = tenant + `deleted_at IS NULL` only. |
| Frontend | `useProducts({ size: 25 })` — no filter params; `ListQuery` has no status field. |

**Conclusion:** Any imported row appears on Products because the catalogue list
is “all live product rows,” not “successfully published listings.”

### Current query condition

```text
products.tenant_id = <ctx> AND products.deleted_at IS NULL
```

### Intended query conditions (Stage 0)

| Surface | Condition |
|---|---|
| **Drafts** | Tenant product with **no** `store_listings` row in `status = synced`. |
| **Products** | Tenant product with **at least one** `store_listings` row in `status = synced`. |

Rationale: Shopify publish already creates/updates `StoreListing` and does
**not** flip `Product.status`. Filtering Products by `status != draft` would
leave successfully published drafts invisible on Products and would still show
unpublished `active` rows if a merchant toggled status via PATCH. Publication
truth is channel-specific and already modelled as `StoreListing`.

Stage 0 uses an **exclusive** projection (any synced listing → Products only).
Per-channel “published here / draft there” dual listing is Stage 7 UI work on
the same product aggregate — not a second table.

---

## 2. Publication-state representation (keep)

| Concern | Existing field | Notes |
|---|---|---|
| Merchant catalogue approval | `Product.status` | `draft` / `active` / `archived` / `unavailable` |
| Channel publication | `StoreListing.status` | `pending` / `synced` / `error` / `removed` |
| Import attempt audit | `ProductImport.status` | Separate from product lifecycle |
| AI optimisation | `Product.ai_status` | Unrelated to Drafts vs Products |

### Lifecycle fields — Stage 0 decision

**No new `lifecycle_status` / `readiness_status` columns in Stage 0.** Existing
enums already cover import outcome, merchant status, and channel sync. Adding
eight parallel timestamps before consumers exist would be speculative schema.
Later stages add readiness scoring and optional timestamps when the editor and
publish flow need them.

### Migration / backfill

| Item | Stage 0 action |
|---|---|
| Product rows | No status rewrite — imports already `draft`. |
| StoreListing | No backfill — `synced` rows already identify published products. |
| Index | Add `ix_store_listings_tenant_product_status` on `(tenant_id, product_id, status)` for EXISTS/NOT EXISTS list queries. |
| Empty Products | Expected after filter until a real Shopify publish succeeds (M17 still open for live OAuth). |

---

## 3. Overlap — do not recreate

Already shipped (trust commits + `docs/PRODUCT_EDITOR_GAP_AUDIT.md` §0):

- Description import + `sanitize_html` + `supplier_description` (0013)
- `PATCH /products/{id}` + `ProductService` + slug 409
- `supplier_title` / `supplier_brand` + `_SYNCED_FIELDS` (0014)
- Identity-preserving variant/image sync (M20)

Still open for later V2 stages: editor UI, variant/image write APIs, pricing
workspace, readiness, AI Studio proposals, idempotent publish UX, premium bulk
tools.

---

## 4. Stage map

| Stage | Scope | Status |
|---|---|---|
| **0** | Audit, this plan, publication query split, drafts nav/list, import history route, counts | **Done** |
| **1** | Premium Drafts grid (columns, bulk bar, readiness preview) | Pending |
| **2** | Extend write APIs / supplier-edit separation beyond PE Stage 2 | Pending (partially done by Claude) |
| **3** | Editor shell + general/description tabs | Pending |
| **4** | Media + structured variants | Pending |
| **5** | Pricing + inventory freshness | Pending |
| **6** | AI Studio + versions + readiness score | Pending |
| **7** | Idempotent publish UX + Products channel columns | Pending |
| **8** | Bulk tools, scheduling, polish | Pending |

---

## 5. Stage 0 deliverables

1. `docs/PRODUCT_WORKSPACE_V2_PLAN.md` (this file).
2. Migration `0015` — listing publication index.
3. `ProductRepository.list_drafts` / `list_published` / `count_workspace`.
4. `GET /api/v1/drafts`, `GET /api/v1/products` (published only), `GET /api/v1/products/workspace-counts`.
5. Frontend routes `/drafts`, `/products` (copy + published list), `/imports/history`.
6. Sidebar: Drafts, Products, Import History under Product Management.
7. Move “Import as Draft” entry point to Drafts (terminology).
8. Tests: imported draft in Drafts only; synced listing surfaces in Products; scoping SQL asserts EXISTS.
9. Update Playwright expectations that assumed imports land on Products.
10. Changelog + roadmap + technical debt note.

Out of Stage 0: premium table columns, editor, pricing engine, AI Studio,
publish wizard, new lifecycle enum rewrite.

---

## 6. Risks

1. **Empty Products page** until a listing is `synced` — correct; document in UI empty state.
2. **E2E breakage** — import flows must target `/drafts`.
3. **ERROR / PENDING listings** stay in Drafts — intentional.
4. **Inventory/pricing pages** may still list all products — out of Stage 0; track as debt.
5. **Stashes** must remain untouched while this branch merges.

---

## 7. Terminology

| Action | Label |
|---|---|
| Supplier ingestion | **Import as Draft** |
| Channel push | **Publish to Store** |
| Bulk channel push | **Publish Selected** |
