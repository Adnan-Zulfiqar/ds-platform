# Phase 4 — completion report

**Product import and catalogue synchronisation.**

Everything below was verified by running the quality gates on 2026-07-31. Where
something has never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Tag | `phase-4-complete` |
| Supplier contract | Captured from live `aliexpress.ds.product.get` |
| Fixture product id | `3256806389000685` |

---

## 1. Architecture summary

```
app/integrations/aliexpress/
├── catalog.py          Pydantic models parsing real supplier payloads
└── product_mapper.py   Supplier DTO → domain row projection

app/models/product.py   products, product_variants, product_images, product_imports
app/repositories/product.py   tenant-scoped catalogue repositories
app/services/product_import.py   import, feed browse, idempotent upsert
app/tasks/products.py   Celery sync_one + sweep_stale (inventory/price foundation)
app/api/v1/products/router.py   list, detail, import, sync, imports, feed browse
```

Dependency flow is unchanged: `api → services → repositories → models`. The
products router is thin — no SQL, no tenant filtering, no AliExpress logic.
Supplier calls go through the existing `AliExpressClient` from Phase 3.

Refreshing a product *is* importing it again. The sync endpoint and the Celery
`products.sync_one` task both delegate to `ProductImportService.import_product`,
so inline refresh and background refresh cannot drift apart.

## 2. Implementation summary

| Capability | Where |
|---|---|
| Contract layer from real payloads | `integrations/aliexpress/catalog.py` + fixtures |
| Domain model + migration 0004 | `models/product.py`, `alembic/versions/0004_*.py` |
| Tenant-scoped repositories | `repositories/product.py` |
| Import service (idempotent) | `services/product_import.py` |
| Product API (6 endpoints) | `api/v1/products/router.py` |
| Background sync foundation | `tasks/products.py` |
| Products page + import dialog | `frontend/app/(protected)/products/` |
| Real API fetchers | `frontend/services/products.ts` |

### API endpoints

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/products` | List catalogue (viewer+) |
| GET | `/api/v1/products/{id}` | Detail with variants and images (viewer+) |
| POST | `/api/v1/products/import` | Import or refresh by supplier id (admin+) |
| POST | `/api/v1/products/{id}/sync` | Refresh an existing row (admin+) |
| GET | `/api/v1/products/imports` | Import attempt history (viewer+) |
| GET | `/api/v1/products/feeds/{feed_name}` | Browse a supplier feed without importing (viewer+) |

### Explicitly foundation only

- **Scheduled drift reconciliation** — `products.sweep_stale` exists and fans out
  to `products.sync_one`, but nothing schedules it yet (no Celery beat entry).
- **Order workflow** — not in scope; no order placement.
- **Store mapping** — columns exist on `products`; no sales channel to map to.

## 3. Commits in this phase

| Commit | Description |
|---|---|
| `c45674b` | AliExpress contract layer from real payloads |
| `f283f56` | Product domain model + migration 0004 |
| `b6ab059` | Repositories, mapper, import service |
| `d9b44e1` | Product API endpoints |
| `4a65e09` | Frontend products module |
| *(this release)* | Celery sync tasks, tests, Playwright import flow, documentation |

## 4. Verification results

### Backend quality gate — **passed**

```
ruff check .          ✅
ruff format --check . ✅
mypy app              ✅ (strict, 85 modules)
pytest                ✅ 419 passed
```

Integration tests drive the real HTTP pipeline with PostgreSQL and replace only
the AliExpress network boundary. Payloads are the **committed fixtures** from
live capture, not invented mocks.

| Test area | Count | Notes |
|---|---|---|
| Repository tenant isolation (SQL compile) | 4 repos × 3 tests | No database required |
| Product import integration | 15 | Real DB + captured payload |
| Tenant isolation integration | 4 | Cross-tenant returns 404, not 403 |
| Sync endpoint integration | 3 | Status preserved on refresh |
| Celery task unit tests | 7 | `.run()` without broker |

### Frontend quality gate — **passed**

```
npm run lint      ✅
npm run typecheck ✅
npm run build     ✅
```

### Playwright — **passed with documented skips**

The import-flow suite seeds connection and catalogue state through the real API.
When the live AliExpress gateway rejects the synthetic OAuth auth code — the
normal case on a developer backend without transport mocking — those three tests
**skip** rather than fail. Parsing and storage are covered by the backend
integration suite.

UI-owned behaviour (routing, empty state, import dialog validation, 409 when not
connected) always runs when the API is reachable.

## 5. Contract discoveries that shaped the design

Documented in [PHASE_4_PLAN.md](PHASE_4_PLAN.md). The ones that would have
broken a mock-based design:

1. Keyword search returns `NGSELECTION_SEARCH_ERROR` — import is by product id
   or feed browse, not search.
2. Prices and counts arrive as strings — `Decimal` everywhere, never `float`.
3. Arrays are double-wrapped (`ae_item_sku_info_d_t_o` inside a plural wrapper).
4. `sku_attr` is a composite variant key that must survive storage verbatim.
5. Supplier HTML description is never exposed — no response schema carries it.

## 6. What was not verified

| Item | Why |
|---|---|
| Celery product sync tasks under a live worker | No RabbitMQ broker or worker on the dev machine |
| Celery beat scheduling for `products.sweep_stale` | Beat not configured in any environment |
| Playwright full import against live AliExpress OAuth | Synthetic auth code rejected by live gateway; tests skip |
| Live import through `AliExpressClient.call` in production | Integration tests use fixture transport; live business call not re-run for this release |
| Docker deployment | C1 — unchanged |

## 7. Technical debt changes

| Item | Change |
|---|---|
| **M10** | **Resolved** — product contract verified through real captured payloads and integration tests |
| **M15** | **Added** — product Celery tasks registered but never executed under a broker |
| **M13** | Unchanged — flaky under parallel load; import-flow tests add three conditional skips |

Full register: [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md).

## 8. Production readiness score

**62 / 100** — feature-complete for single-product import and on-demand refresh;
not yet production-ready for bulk catalogue operations or scheduled sync.

| Dimension | Score | Notes |
|---|---|---|
| Architecture | 90 | Layering preserved; sync reuses import |
| Security / tenancy | 85 | Isolation tests on all four repositories; 404 not 403 |
| Supplier contract | 80 | Real payloads pinned; live client path partially verified |
| Background jobs | 40 | Tasks exist; broker, worker, and beat unverified |
| Deployment | 20 | C1 — Docker path never executed |
| Test coverage | 75 | 419 backend + Playwright UI paths; conditional skips documented |
| Observability | 30 | Structured logs; no metrics or alerting |

**Blockers before production catalogue sync:** C1 (deployment verification),
Celery worker + beat in a running environment, and a decision on sync interval.

---

See also: [PHASE_4_PLAN.md](PHASE_4_PLAN.md), [CHANGELOG.md](../CHANGELOG.md),
[PROJECT_ROADMAP.md](../PROJECT_ROADMAP.md).
