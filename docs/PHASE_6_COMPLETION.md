# Phase 6 — completion report

**Inventory management, dynamic pricing, multi-store automation & shipment tracking.**

Everything below was verified by running the quality gates on 2026-07-31. Where
something has never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Tag | `phase-6-complete` |
| Migration | `0006` — operations platform domain |
| Live client verification | `AliExpressClient.call()` for inventory dependency (`product.get`) |

---

## 1. Architecture summary

```
app/models/          store, inventory, pricing, automation, notification, analytics
app/repositories/    tenant-scoped repos + ProductRepository inventory helpers
app/services/        inventory_sync, pricing_engine, store_service,
                     automation_service, notification_service, analytics_service
app/api/v1/          stores, inventory, pricing, automation, notifications, analytics
app/tasks/           inventory, pricing, automation, shipments, analytics, notifications
```

Dependency flow is unchanged: `api → services → repositories → models`. Handlers
remain thin. Tenant isolation stays in `TenantScopedRepository`.

**Design decisions preserved**

- A **store** is a sales channel (Shopify / Woo / … / manual), not AliExpress.
  Supplier credentials stay on `aliexpress_connections`.
- Inventory sync **reuses** `ProductImportService.import_product` — stock refresh
  cannot drift from catalogue import.
- Automation **dispatches** to existing services; it does not reimplement sync.
- Pricing scope precedence: product → category → store → global.
- Currency conversion is an identity hook until a real FX source lands.
- Dashboard `MOCK_*` data removed; charts consume `GET /analytics/dashboard`.

---

## 2. Implementation summary

| Capability | Where |
|---|---|
| Migration 0006 | `alembic/versions/20260731_2218_0006_*.py` |
| Store CRUD + health + statistics | `services/store_service.py`, `/stores` |
| Inventory sync + history | `services/inventory_sync.py`, `/inventory` |
| Pricing rules, preview, apply, audit | `services/pricing_engine.py`, `/pricing` |
| Automation rules + runs | `services/automation_service.py`, `/automation` |
| Notifications | `services/notification_service.py`, `/notifications` |
| Analytics dashboard | `services/analytics_service.py`, `/analytics/dashboard` |
| TrackingEvent on status change | `services/order_sync.py` |
| Celery tasks + beat | `tasks/*`, `workers/celery_app.py` |
| Frontend pages | inventory, pricing, stores, automation, notifications, analytics, shipments |
| Live dashboard / analytics | `services/dashboard.ts` — no `MOCK_*` remaining |

### API surface (Phase 6)

| Area | Endpoints |
|---|---|
| Stores | list, statistics, create, get, patch, delete, health |
| Inventory | list, sync, sync-runs, changes |
| Pricing | rules CRUD, preview, apply, changes |
| Automation | rules CRUD, run, runs list |
| Notifications | list, unread-count, mark read, read-all |
| Analytics | dashboard |

---

## 3. Live API verification

Executed via `backend/scripts/verify_phase6_live.py` and
`backend/scripts/verify_orders_live.py` using stored credentials and
**`AliExpressClient.call()`** — not raw HTTP.

| Call | Result | Relevance |
|---|---|---|
| `aliexpress.ds.product.get` | **Success** (captured `product_get_phase6_live.json`) | Inventory sync dependency |
| `aliexpress.ds.category.get` | **Success** | Client path still healthy |
| `aliexpress.ds.trade.order.get` | Documented error envelope | Order/shipment refresh path |
| `aliexpress.ds.commissionorder.listbyindex` | Rate-limited this run | Client retry path exercised |

**No new AliExpress methods were introduced in Phase 6.** Inventory reuses
product import; shipment refresh aliases order status refresh.

**Not verified live:** dedicated carrier tracking APIs (none integrated —
tracking events come from order status sync); real FX conversion; Shopify/Woo
OAuth (manual store registration only).

---

## 4. Tests executed

| Gate | Result |
|---|---|
| `ruff check` / `ruff format --check` | Pass |
| `mypy app` (strict) | Pass (129 modules) |
| `pytest` | **566 passed** |
| Frontend `lint` / `typecheck` / `build` | Pass |
| Playwright (`phase6-ops` + `shell`, chromium) | **24 passed** (one register flake under parallelism — M13 — passed on re-run) |

New coverage includes unit tests for the pricing engine, Phase 6 repository
tenant scoping, Celery task registration, and integration tests for stores,
pricing, notifications, analytics, automation, and inventory list.

---

## 5. Known limitations

| Item | Status |
|---|---|
| Celery never run under live RabbitMQ (M15) | Open |
| Webhook unsigned (M11) | Open |
| Docker never built on this machine (C1) | Open |
| Store channel OAuth (Shopify etc.) | Manual stores only |
| FX conversion | Identity hook |
| Analytics SQL in service layer | Acceptable for Phase 6; move to repos if a second caller appears |
| Zustand notification store unused | Server state moved to React Query; delete store in cleanup |

---

## 6. Readiness

| Score | Value | Notes |
|---|---|---|
| Production readiness | **72/100** | Ops APIs and UI land; C1/M11/M15 still block production confidence |
| Operational readiness | **70/100** | Automation + notifications exist; beat/worker path unverified under broker |

---

## 7. Recommendation for Phase 7

Do **not** expand the operations surface further until:

1. Celery tasks run once under a real broker (retire or narrow M15).
2. Store channel OAuth for at least one marketplace, or an explicit decision to
   stay manual-first.
3. Optional: move analytics aggregation fully into repositories; delete the
   unused Zustand notification store.
