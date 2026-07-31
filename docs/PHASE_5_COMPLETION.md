# Phase 5 — completion report

**Order management, fulfilment, and synchronisation.**

Everything below was verified by running the quality gates on 2026-07-31. Where
something has never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Tag | `phase-5-complete` |
| Migration | `0005` — order management tables and enums |
| Live client verification | `AliExpressClient.call()` against the live gateway |

---

## 1. Architecture summary

```
app/integrations/aliexpress/
├── orders.py           Wire models + parsers (live envelopes + documented body)
├── client.py           error_response envelope unwrap (live finding)
└── webhook.py          Replay protection, classification, counters

app/models/order.py     Order, OrderItem, Shipment, TrackingEvent, OrderEvent, OrderSyncRun
app/repositories/order.py   Tenant-scoped repositories
app/services/order_sync.py  Idempotent sync, timeline, statistics
app/tasks/orders.py     sync_all, sync_one_store, refresh_status, cleanup
app/api/v1/orders/router.py   list, statistics, sync, detail, timeline
```

Dependency flow is unchanged: `api → services → repositories → models`. Handlers
remain thin. Tenant isolation stays in `TenantScopedRepository`.

A newly imported order is created **at** the supplier's current fulfilment
state. Transition validation applies to updates only — the order lived its
earlier lifecycle upstream before this platform saw it.

Webhook deliveries never mutate order state from an unsigned payload. They
register for replay protection, count activity, and may nudge a sync. Tenant
attribution from the payload is not trusted (M11/M12).

## 2. Implementation summary

| Capability | Where |
|---|---|
| Live client verification + contract layer | `scripts/verify_orders_live.py`, `integrations/aliexpress/orders.py` |
| Domain model + migration 0005 | `models/order.py`, `alembic/versions/0005_*.py` |
| Tenant-scoped repositories | `repositories/order.py` |
| Sync service (idempotent, incremental) | `services/order_sync.py` |
| Order API (5 endpoints) | `api/v1/orders/router.py` |
| Celery order tasks + beat entries | `tasks/orders.py`, `workers/celery_app.py` |
| Webhook replay protection | `integrations/aliexpress/webhook.py` |
| Orders UI + detail + statistics | `frontend/components/orders/`, `/orders`, `/orders/[orderId]` |
| Live dashboard order status row | `OrderStatisticsCards` on dashboard + orders page |

### API endpoints

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/orders` | List with filters (viewer+) |
| GET | `/api/v1/orders/statistics` | Live status dashboard payload (viewer+) |
| POST | `/api/v1/orders/sync` | Incremental sync window (admin+) |
| GET | `/api/v1/orders/{id}` | Detail with items, shipments, tracking (viewer+) |
| GET | `/api/v1/orders/{id}/timeline` | Merged lifecycle + tracking history (viewer+) |

### Fulfilment lifecycle

`pending → awaiting_payment → paid → processing → fulfilled → shipped → delivered`,
with `cancelled`, `refunded`, and `disputed` as validated exits. Terminal states
reject further transitions except documented exceptions (e.g. delivered → disputed).

## 3. Live API verification (mandatory)

Executed via `backend/scripts/verify_orders_live.py` using a stored
`AliExpressConnection`, decrypted credentials, and **`AliExpressClient.call()`**
— not raw HTTP.

| Call | Result | What it proved |
|---|---|---|
| `aliexpress.ds.category.get` | **Success** (HTTP 200, parsed body) | Request building, signing, response parsing on the production client path |
| `aliexpress.ds.trade.order.get` (fabricated id) | Documented error envelope | Error mapping; live gateway wraps failures in `error_response` |
| `aliexpress.ds.commissionorder.listbyindex` | Live list response captured | List contract bytes from the real gateway |

**Live finding that changed production code:** the gateway wraps documented
failures in `{"error_response": {...}}`. The client previously validated only
the top level and treated an `ApiCallLimit` body as success, silently skipping
the rate-limit retry. Fixed in `client.py` with regression tests.

**Not verified live:** a populated order-detail success body. The sandbox account
has no real orders, so the happy-path order body is documentation-derived
(debt **M16**). Captured fixtures cover the error and list envelopes that the
live gateway did return.

## 4. Commits in this phase

| Commit | Description |
|---|---|
| `ef83e4b` | Live order API verification + order contract layer + envelope fix |
| `b6a1f46` | Order domain model, migration 0005, schemas, lifecycle tests |
| `ae17480` | Tenant-scoped order repositories + isolation tests |
| `e6f406e` | Sync service + API endpoints + integration tests |
| `ebaca17` | Celery order tasks + beat schedule |
| `4021a57` | Webhook order processing with Redis replay protection |
| *(this release)* | Frontend orders module, dashboard live status, Playwright, docs |

## 5. Verification results

### Backend quality gate — **passed**

```
ruff check .          ✅
ruff format --check . ✅
mypy app              ✅ (strict)
pytest                ✅ 523 passed
```

### Frontend quality gate — **passed**

```
npm run lint      ✅
npm run typecheck ✅
npm run build     ✅
```

### Playwright — **passed with known flakiness (M13)**

Orders suite: **16 passed** (sidebar, empty state, sync dialog validation,
sync failure when disconnected, filter wire contract, detail 404, route
protection, 320px overflow).

Full suite still exhibits M13 timing flakes under maximum parallelism; focused
re-runs of the Phase 5 and related shell tests are green. Import-flow product
tests continue to skip when live OAuth rejects the synthetic code.

## 6. What was not verified

| Item | Why |
|---|---|
| Populated order-detail success payload from live API | No orders on the sandbox account (M16) |
| Celery order tasks under a live broker/worker | No RabbitMQ worker on the dev machine (M15 extended) |
| Webhook signature verification | AliExpress deliveries remain unsigned (M11) |
| Tenant attribution from webhook payload | No verifiable tenant claim in the push (M11) |
| Docker deployment | C1 — unchanged |
| End-to-end browser sync that imports real supplier orders | Requires live orders + connected OAuth; backend integration covers sync against captured shapes |

## 7. Technical debt changes

| Item | Change |
|---|---|
| **M10 remainder** | Narrowed further — production `AliExpressClient.call()` verified live for category success + order error/list paths |
| **M11** | Updated — handler is no longer inert; replay protection and counters exist; still must not trust unsigned payloads to mutate state |
| **M15** | Extended — order Celery tasks registered and unit-tested; still never run under a broker |
| **M16** | **Added** — populated order success body is documentation-derived |
| **M9** | Partially eased — order synchronisation row on the dashboard is live; charts and mock stat cards remain sample data |

Full register: [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md).

## 8. Production readiness score

**68 / 100** — feature-complete for order sync, list/detail/timeline, fulfilment
lifecycle, and operator UI; not yet production-ready for unsigned webhook-driven
mutation or broker-backed scheduled sync at customer scale.

| Dimension | Score | Notes |
|---|---|---|
| Architecture | 90 | Layering preserved; sync reuses client |
| Security / tenancy | 85 | Isolation tests; cross-tenant 404; webhook does not trust payload for writes |
| Supplier contract | 70 | Live client path verified; order success body not live-captured |
| Background jobs | 45 | Tasks + beat entries exist; broker/worker unverified |
| Deployment | 20 | C1 — Docker path never executed |
| Test coverage | 80 | 523 backend + orders Playwright UI paths |
| Observability | 35 | Structured logs + webhook counters; no metrics/alerting |

**Blockers before production order sync at scale:** C1, Celery worker + beat in a
running environment, webhook signing confirmation (or continued “nudge only”
policy), and a live populated order fixture when an account has real orders.

**Phase 6 readiness:** Yes — the order domain, sync path, and UI are in place for
store mapping / fulfilment automation work. Do not begin Phase 6 until that
phase’s own prompt arrives.

---

See also: [CHANGELOG.md](../CHANGELOG.md), [PROJECT_ROADMAP.md](../PROJECT_ROADMAP.md),
[TECHNICAL_DEBT.md](TECHNICAL_DEBT.md).
