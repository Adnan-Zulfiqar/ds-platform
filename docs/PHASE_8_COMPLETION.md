# Phase 8 — completion report

**Shopify sales-channel integration.**

| | |
|---|---|
| Date | 2026-08-01 |
| Branch | `develop` |
| Tag | `phase-8-complete` → `4a4d5d7` |
| Base | `phase-7-complete` (`1ed5477`) |
| Migration | `0008` — shopify_connections, store_listings, orders.store_id |

### Commits

| Hash | Message |
|---|---|
| `b4cf4a4` | feat(shopify): add connection schema and tenant-scoped repositories |
| `6476fe7` | feat(shopify): OAuth, Admin sync, webhooks, and Celery tasks |
| `665f848` | feat(frontend): Shopify connect card and integrations status UI |
| `4a4d5d7` | docs: close Phase 8 Shopify sales-channel integration |

---

## 1. What shipped

| Area | Outcome |
|---|---|
| OAuth | Install URL, callback HMAC, Redis state, encrypted token, Store binding, disconnect |
| Connection UI | Shopify card on Integrations (connect / status / errors / disconnect) |
| Product publish | Idempotent via `store_listings` |
| Inventory / price push | Sync service + Celery tasks |
| Order import | Poll + webhook upsert; **no fulfilment** |
| Webhooks | HMAC verify, replay NX, topic registration on connect |
| Celery | `shopify.*` tasks + hourly order sync beat |

---

## 2. Live verification

| Check | Status |
|---|---|
| Unit HMAC / scoping tests | Pass |
| Full pytest | **592 passed** |
| Frontend lint / typecheck / build | Pass |
| Playwright integrations (chromium) | **11 passed** |
| Live Shopify OAuth / Admin API | **Not run** — no Partner app credentials on this machine |

Mocks only for unit tests. Document this gap honestly.

---

## 3. Production readiness (Shopify slice)

| Area | Score | Notes |
|---|---|---|
| OAuth + security model | 7 | Pattern mirrors AliExpress; unproven against live Shopify |
| Catalogue publish | 6 | REST Admin API; needs live shop validation |
| Inventory / price | 6 | Implemented; location selection is first location only |
| Order import | 6 | Foundation only; no fulfilment |
| Webhooks | 7 | HMAC required; needs live delivery confirmation |
| **Overall Phase 8** | **~6.5 / 10** | Ready for Partner app credentials + staging shop |

---

## 4. Remaining limitations

1. No live Shopify Partner app configured locally.
2. Inventory updates use the shop’s first location only.
3. Product/inventory inbound webhooks are acknowledged but DropPilot remains
   source of truth for catalogue pushes.
4. Fulfilment / tracking push deferred.
5. Fourth unscoped repository (`ShopifyMaintenanceRepository`) for Celery sweeps
   — documented; HTTP must not use it.

---

## 5. Docs

- `docs/PHASE_8_PLAN.md`
- `docs/SHOPIFY_INTEGRATION.md`
- Updated roadmap, changelog, technical debt, store-channel decision
