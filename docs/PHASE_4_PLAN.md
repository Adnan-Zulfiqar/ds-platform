# Phase 4 — implementation plan

**Product import and catalogue synchronisation.**

Written after capturing real AliExpress payloads, not from documentation. The
contract findings below are the reason this plan looks the way it does.

---

## 1. Contract discovery (done first, deliberately)

M10 said the response contract had never been verified. It has now been, and it
changed the design.

### Discovery paths — what actually works

| Path | Result |
|---|---|
| `aliexpress.ds.feedname.get` | **124 real feed names** |
| `aliexpress.ds.recommend.feed.get` with a real feed | **works** — 7,717 products in one feed |
| `aliexpress.ds.recommend.feed.get` with `DS_bestseller` | 0 records — that name was invented, not real |
| `aliexpress.ds.text.search` by **keyword** | **`NGSELECTION_SEARCH_ERROR` — unavailable** |
| `aliexpress.ds.text.search` by **`categoryId`** | works (`rsp_code=00`) |
| `aliexpress.ds.product.get` | **works** — 13,590-byte payload |
| `aliexpress.ds.category.get` | works — 549 categories |

**Keyword search is not available on this account.** Product discovery therefore
goes through *feeds* and *category browse*, and the import design follows that
rather than assuming a search box.

### Payload traps that mocks would not have caught

1. **`image_urls` is a semicolon-delimited string**, not an array.
2. **Every price is a string** (`"3.30"`). Parsing to `Decimal`, never `float`.
3. **Counts are strings** (`sales_count: "112"`), ratings too (`"4.9"`).
4. **Arrays are double-wrapped**: `ae_item_sku_info_dtos.ae_item_sku_info_d_t_o`,
   `ae_sku_property_dtos.ae_sku_property_d_t_o`,
   `ae_item_properties.ae_item_property`, `products.traffic_product_d_t_o`.
5. **`product_id` is an integer** in responses but a string in requests.
6. **`sub_product_id` is a JSON string** holding a country map:
   `{"US":3256806389000685}`, distinct from `main_product_id`.
7. **Feed items and detail items use different field names** for the same
   concepts: `product_title` vs `subject`, `sale_price` vs `offer_sale_price`.
8. `sku_attr` is a composite key: `10:529#Realme GT Neo5;14:771#tyjt white`.

Captured payloads are committed as test fixtures so these are pinned by tests
rather than by memory.

---

## 2. Scope

Built in this phase:

| Area | Deliverable |
|---|---|
| Contract layer | Pydantic models parsing the **real** payloads, with fixtures |
| Data model | `products`, `product_variants`, `product_images`, migration `0004` |
| Repositories | Tenant-scoped, with isolation tests |
| Import service | Import one product by id, using the **existing** client |
| Catalogue sync | Re-import to refresh price and stock |
| Background jobs | Celery tasks, idempotent |
| API | Product list, detail, import, sync |
| Audit logging | Who imported/changed what, per tenant |
| Frontend | Products page backed by real data |

Explicitly **foundation only** (structure without full behaviour), and labelled
as such wherever it appears:

- **Order workflow** — model and status vocabulary only. No order placement.
  Placing a real order spends real money and is not something to build against
  an unverified contract.
- **Inventory/price sync** — the mechanism and storage exist and run on demand;
  scheduled drift reconciliation is not built.
- **Store mapping** — `products` carries the mapping columns, but no sales
  channel exists yet to map *to*.

---

## 3. Dependencies

| Depends on | Status |
|---|---|
| `AliExpressClient` (Phase 3) | Reused. No new integration logic. |
| `build_signed_params` / `signing_path_for` | Reused unchanged. |
| Fernet encryption | Reused for stored credentials. |
| `TenantScopedRepository` | Reused — the multi-tenancy boundary. |
| Celery + Redis | Reused. Redis now verified working. |
| A connected AliExpress account | Present, tokens valid to 2026-08-30. |

---

## 4. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Keyword search unavailable | **High** | Design import around feeds and category browse; do not ship a search box that cannot work |
| Response shape varies by product | **High** | Every field that is not structurally guaranteed is optional; parse defensively and record failures |
| Price as string → float rounding | **High** | `Decimal` everywhere; never `float` for money |
| Token expires 2026-08-30 | Medium | Refresh path exists but is unverified live (carried debt) |
| Feed contents change | Medium | Import is idempotent and keyed on `(tenant_id, aliexpress_product_id)` |
| Rate limits under bulk import | Medium | Existing outbound limiter, which fails closed |

---

## 5. Technical debt affecting Phase 4

| Item | Effect |
|---|---|
| **M10** | Being closed by this phase for the product endpoints |
| **M11/M12** | Webhook unsigned and unthrottled — order events will eventually arrive here |
| **M13** | Flaky E2E; new tests must not add to it |
| **C1** | Docker unbuilt — nothing here is deployment-verified |
| **M4** | Frontend API types hand-written; new types must stay in step |

---

## 6. Sequence

1. Contract schemas + fixtures from captured payloads
2. Models + migration `0004`
3. Repositories + isolation tests
4. Import service
5. Celery tasks
6. API endpoints
7. Audit logging
8. Frontend
9. Docs, quality gates, tag

Quality gates run after each stage, not only at the end.
