# Changelog

All notable changes to DropPilot AI.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses phase tags rather than semantic versions until the first
production release.

---

## [Unreleased]

### Fixed

- **A-02** — Logout (and mid-session token clear) call `queryClient.clear()` so
  a shared browser cannot show the previous tenant's React Query cache;
  `router.refresh()` alone was insufficient.
- **A-05** — Compose frontend defaults to `NEXT_PUBLIC_API_URL=http://localhost`
  so the SPA uses nginx same-origin `/api`; CORS includes the nginx origin; CI
  smoke asserts the bundle does not embed `:8000`.
- **A-04** — Shopify product publish is Celery-safe: create uses deterministic
  handle `droppilot-{product_id}` and adopts an existing Shopify product on
  redelivery instead of posting a duplicate.
- **A-03** — Platform-global AI prompt create / version / activate are refused
  unless `AI_ALLOW_PROMPT_MUTATION=true` (default off). Tenant admins can still
  list, history, and test-render. Tests opt in via conftest.
- **A-01** — Shopify `shop_domain` is globally unique (migration `0012`);
  webhooks resolve the owning tenant by indexed domain lookup instead of a
  capped table scan; connect rejects a shop already bound to another workspace.
- AliExpress connect is platform-credential only: merchants no longer enter App
  Key / App Secret. OAuth uses `ALIEXPRESS_APP_*` from the environment; tenant
  rows store encrypted seller tokens only (migration `0009`).
- Shopify connect rejects custom storefront domains; OAuth callback failures are
  classified (`hmac` / `state` / `exchange`) without logging secrets. See
  [SHOPIFY_CONNECTION_DEBUG_REPORT.md](docs/SHOPIFY_CONNECTION_DEBUG_REPORT.md).

### Added

- **Phase 9 Stage 3** — product optimisation data architecture: migration
  `0011` (SEO/marketplace/AI columns + `product_versions`),
  `ProductOptimizationService` (StubProvider via Stage 2 prompts), versions /
  optimize / activate APIs, products-table AI status + Optimize + History UI.
  See [PHASE_9_STAGE_3_COMPLETION.md](docs/PHASE_9_STAGE_3_COMPLETION.md).

---

## [phase-8] — 2026-08-01

Shopify as the first sales channel. See
[PHASE_8_COMPLETION.md](docs/PHASE_8_COMPLETION.md) and
[SHOPIFY_INTEGRATION.md](docs/SHOPIFY_INTEGRATION.md).

### Added

- Migration `0008`: `shopify_connections`, `store_listings`, `orders.store_id`
- OAuth connect/callback/status/disconnect; Fernet-encrypted access tokens
- Product publish, inventory/price push, order import (poll + webhooks)
- Celery `shopify.*` tasks and beat entry
- Integrations UI Shopify card

### Verified

- Backend: ruff, mypy strict, **592** pytest
- Frontend: lint, typecheck, build
- Playwright integrations (chromium): **11** passed
- Live Shopify Admin/OAuth: **not run** (no Partner credentials)

---

## [phase-7] — 2026-07-31

Production hardening and operational readiness. See
[PHASE_7_COMPLETION.md](docs/PHASE_7_COMPLETION.md).

### Added

- Compose **beat** service and worker healthcheck
- CI on `develop`: image builds, `docker compose config`, Celery broker job,
  best-effort compose smoke
- `workers.health` task + `scripts/verify_celery_broker.py`
- Webhook HMAC (opt-in) and shed-without-429 limiter
- Email verification foundation: migration `0007`, logging mailer,
  `/auth/verify-email/*`, `RequireVerified` (flag-gated)
- `docs/STORE_CHANNEL_DECISION.md`, `docs/PHASE_7_PLAN.md`

### Changed

- Access tokens carry `email_verified`
- Frontend: removed unused notification Zustand store; removed stale Import nav

### Verified (local)

- Backend: ruff, mypy strict, pytest (583+)
- Frontend: lint, typecheck, build
- Live AliExpress and local Docker/Celery: **not** available on this machine —
  CI path documented

---

## [phase-6] — 2026-07-31

Inventory synchronisation, dynamic pricing, multi-store management, automation,
notifications, real analytics, and shipment tracking extensions.

Closed with migration `0006`, integration tests against real PostgreSQL, live
`AliExpressClient.call()` for the inventory dependency, and frontend pages for
every new module. See [PHASE_6_COMPLETION.md](docs/PHASE_6_COMPLETION.md).

### Added

**Domain** (`migration 0006`)
- `stores`, inventory sync runs/changes, pricing rules/changes, automation
  rules/runs, notifications, analytics daily rollups
- Product `sell_price` and optional `store_id`

**Services & APIs**
- Inventory sync (idempotent, reuses product import)
- Pricing engine (percentage / fixed / tiered; min profit / max price guards;
  preview before apply; audit trail)
- Store management with health and statistics
- Automation dispatcher (background-oriented)
- Notification centre
- Analytics dashboard aggregates

**Celery**
- `inventory.sync`, `pricing.recalculate`, `automation.run`,
  `shipment.refresh`, `analytics.aggregate`, `cleanup.old_notifications`

**Frontend**
- `/inventory`, `/pricing`, `/stores`, `/automation`, `/notifications`,
  `/analytics`, `/shipments`
- Dashboard and analytics consume live `/analytics/dashboard` — `MOCK_*` removed

### Verified

- **566 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — `product.get` success for inventory path;
  category / order error path re-checked
- Frontend lint, typecheck, build
- Tenant isolation tests for new repositories
- Playwright: Phase 6 ops smoke + updated shell dashboard tests (chromium)

### Not verified

- Celery under a live RabbitMQ broker (M15)
- Webhook signature verification (M11)
- Docker deployment (C1)
- Marketplace OAuth for sales channels (manual stores only)

---

## [phase-5] — 2026-07-31

Order management, fulfilment, and synchronisation from AliExpress.

Closed with live `AliExpressClient.call()` verification, migration `0005`,
integration tests against real PostgreSQL, and an Orders UI backed by the real
API. See [PHASE_5_COMPLETION.md](docs/PHASE_5_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/orders.py`)
- Wire models and parsers for order detail, commission list, and logistics
- Captured live fixtures for error envelopes and list responses
- Client fix: unwrap `error_response` envelopes before error mapping

**Domain model** (`app/models/order.py`, migration `0005`)
- `orders`, `order_items`, `shipments`, `tracking_events`, `order_events`,
  `order_sync_runs`
- Validated fulfilment transition map

**Sync service** (`app/services/order_sync.py`)
- Idempotent incremental import; timeline merge; statistics

**Order API** (`app/api/v1/orders/router.py`)
- List (filters), statistics, sync, detail, timeline

**Background sync** (`app/tasks/orders.py`)
- `orders.sync_all`, `orders.sync_one_store`, `orders.refresh_status`,
  `orders.cleanup` with Celery beat entries

**Webhook processing**
- Redis replay protection, classification, delivery counters; unsigned payloads
  never mutate order state directly

**Frontend orders module**
- `/orders` list with filters, search, pagination, sync dialog, live statistics
- `/orders/[orderId]` detail with items, shipments, tracking, timeline
- Dashboard live order-synchronisation row

### Verified

- **523 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — category success; order get error path;
  commission order list capture
- **Tenant isolation** — SQL compile tests on order repositories; integration
  cross-tenant 404
- **Frontend lint, typecheck, build** — all pass
- **Playwright orders suite** — 16 passed against real backend

### Not verified

- Populated order-detail success body from live API (M16)
- Celery order tasks under a live broker/worker (M15)
- Webhook signature verification (M11 — unsigned)
- Docker deployment (C1 — unchanged)

---

## [phase-4] — 2026-07-31

Product import and catalogue synchronisation from AliExpress.

Closed with contract discovery from live payloads, integration tests against
real PostgreSQL, and a products UI backed by the real API. See
[PHASE_4_COMPLETION.md](docs/PHASE_4_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/catalog.py`)
- Pydantic models parsing real `aliexpress.ds.product.get` and feed payloads
- Captured fixtures committed under `tests/fixtures/aliexpress/`

**Domain model** (`app/models/product.py`, migration `0004`)
- `products`, `product_variants`, `product_images`, `product_imports`
- Unique constraint on `(tenant_id, source, external_id)` for idempotent import

**Import service** (`app/services/product_import.py`)
- Import by supplier product id; feed browse without importing
- Idempotent upsert preserving tenant-set status on refresh

**Product API** (`app/api/v1/products/router.py`)
- List, detail, import, sync, import history, feed browse

**Background sync foundation** (`app/tasks/products.py`)
- `products.sync_one` — refresh one product from its supplier
- `products.sweep_stale` — fan out refresh for stale catalogue rows

**Frontend products module**
- `/products` page with table, empty state, and import dialog
- Real API fetchers in `services/products.ts`

### Verified

- **419 backend tests** — ruff, mypy strict, pytest all green
- **Tenant isolation** — SQL compile tests on four repositories; integration
  tests confirm cross-tenant access returns 404 not 403
- **Real payload parsing** — integration tests use committed capture, not
  invented JSON
- **Frontend lint, typecheck, build** — all pass
- **Playwright** — UI paths pass; import-flow tests skip when live OAuth
  callback cannot complete (documented)

### Not verified

- Celery product sync tasks under a live broker/worker
- Celery beat scheduling for stale-product sweeps
- Live import through production `AliExpressClient.call` (fixture transport in tests)
- Docker deployment (C1 — unchanged)

---

## [phase-3] — 2026-07-31

AliExpress integration foundation. Connection, credentials, client and inbound
webhook only — no product, price, inventory, or order functionality.

Closed with live verification against the real AliExpress gateway (sub-phases
3.5–3.7). See [PHASE_3_COMPLETION.md](docs/PHASE_3_COMPLETION.md).

### Verified live

- **OAuth round trip** completed end to end against `api-sg.aliexpress.com`,
  storing encrypted access and refresh tokens
- **Replay protection** — an OAuth state is deleted on first use and a second
  presentation is refused
- **API permissions** — product, category, search, order and freight endpoints
  reachable; affiliate correctly denied, verified against a known-good denial
  control rather than by absence of evidence
- **Webhook** reachable through `https://api.whiteto.com` and answering 200 for
  every malformed input tried

### Added

**Inbound webhook** (`app/integrations/aliexpress/webhook.py`)
- `POST /api/v1/integrations/aliexpress/webhook`, separate from the OAuth
  callback — different method, caller, contract and response
- Always answers 200, including on an unreadable body, because a delivery agent
  reads the status as a retry instruction
- Logs field names and counts, never payload values: an order notification
  carries buyer names and addresses
- Signature verification is **not** implemented; recorded as M11

### Fixed (during live verification)

- `sign_method` was excluded from the signature base string, which made every
  token exchange fail with `IncompleteSignature` (`fa02dd2`)
- Only the root `Settings` read `.env`, so every nested settings group silently
  ignored the file and ran on defaults (`ba752c9`)
- The OAuth callback required a Bearer token that a browser redirect from
  AliExpress can never carry (`74c6653`)
- Redis connections failed on every request because redis-py negotiates RESP3
  with `HELLO`, which the local server rejects (`74c6653`)

### Added

**Credential encryption** (`app/core/encryption.py`)
- Fernet (AES-128-CBC + HMAC-SHA256, random IV) for third-party credentials
- Key rotation via `MultiFernet`: keys newest-first, decryption tries each, so
  rotation needs no downtime
- Fails closed when no key is configured — refusing beats storing a customer's
  supplier secret in plaintext

**Database** — migration `0003`
- `aliexpress_connections` with encrypted secret and token columns, status,
  expiry, and last-sync tracking. Unique per tenant

**Integration package** (`app/integrations/aliexpress/`)
- Signed HTTP client with timeouts, jittered exponential backoff, and typed
  error mapping. Retries only failures that could resolve themselves
- Inspects the response body regardless of status, because AliExpress reports
  failure inside HTTP 200 as often as through a status code
- OAuth signing and a single-use, server-side, random `state` token

**Outbound rate limiting** (`app/integrations/rate_limiter.py`)
- Per tenant and provider. **Fails closed**, the opposite of the inbound
  limiter: exceeding a provider's quota can suspend the application key for
  every tenant

**Endpoints**
- `POST /api/v1/integrations/aliexpress/connect` (admin or owner)
- `GET /api/v1/integrations/aliexpress/callback`
- `GET /api/v1/integrations/aliexpress/status`
- `DELETE /api/v1/integrations/aliexpress/disconnect` (admin or owner)

**Background tasks** — `health_check` and `sweep_health_checks`, with tenant
context bound per connection; integration work routed to its own queue

**Frontend** — `/settings/integrations` with real server-driven connection
state, and a settings index that is now a genuine hub rather than a placeholder

**Documentation** — `docs/ALIEXPRESS_INTEGRATION.md`

### Changed

- Disconnect **hard-deletes** the connection, unlike everything else in the
  platform. A customer who disconnects has asked us to forget their credentials
- Celery `task_routes` sends `integrations.*` to a dedicated queue

### Fixed

- **Docker images installed a stub `app` package into site-packages** (debt item
  M1). The real code only won by `sys.path` ordering, so any command run from a
  different directory resolved to the empty stub. The builder now installs
  dependencies only, extracted from `pyproject.toml`

---

## [phase-2-complete] — 2026-07-31

Application shell, navigation, and SaaS UI foundation. Frontend only; no
business functionality.

### Added

**Application shell**
- `AppShell` — sidebar, top bar, and a main region that owns scrolling so the
  chrome stays put without `position: fixed`
- Collapsible desktop sidebar with six navigation sections, tooltips when
  collapsed, and a persisted collapse preference
- Responsive drawer below `md`, sharing the same navigation component as the
  desktop rail so the two cannot diverge; closes on navigation and when the
  viewport grows past the breakpoint
- Top bar with notification centre, theme toggle, and a user menu showing name,
  email, tenant, and role

**Navigation**
- `lib/navigation.ts` — one manifest feeding sidebar, drawer, and top bar. Each
  entry declares whether its destination exists; unbuilt ones render as
  non-interactive items, so primary navigation can never reach a 404

**Dashboard**
- Six stat cards with trend indicators that decouple direction from sentiment,
  so a metric where down is good is not painted red
- Three reusable charts — sales area, stacked orders, horizontal product
  performance — on a shared chart theme that reads design tokens at runtime, so
  a theme switch recolours them with no JavaScript
- `ChartContainer` owning all four chart states: loading, error, empty, populated

**Design system** — nine primitives: `avatar`, `tooltip`, `sheet`, `separator`,
`empty-state`, `error-state`, `page-header`, `coming-soon`, plus `stat-card` and
`chart-container` under `components/dashboard/`

**Routes** — `/products`, `/stores`, `/orders`, `/analytics`, `/settings`,
`/unauthorized`, plus loading and error boundaries scoped to the protected group

**State and services** — `notification-store`; `services/dashboard.ts`,
`products.ts`, `stores.ts` as query keys and types with no fetchers, because
those endpoints do not exist

**Testing** — 47 Playwright tests covering sidebar, mobile navigation, theme
switching, protected routes, dashboard, and user menu at 320px, 768px, and
1440px. They register real accounts through the API rather than stubbing it, so
they exercise the actual token and cookie handling

### Changed

- `layouts/sidebar.tsx` and `layouts/top-nav.tsx` moved into
  `components/navigation/` and split into focused components
- `middleware.ts` treats `/unauthorized` as public — it reports a permission
  failure, not an authentication one

### Fixed

- **`CORS_ORIGINS` could not be set in the documented format.**
  pydantic-settings runs `json.loads` on list-typed fields before validators
  execute, so the comma-separated form in `.env.example` raised
  `JSONDecodeError` during boot. The application could not start with its own
  example configuration. Fixed with `NoDecode` plus a validator accepting both
  forms, and covered by regression tests
- The user menu showed the email address twice for accounts with no name set

---

## [phase-1.1] — 2026-07-31

Pre-Phase-2 housekeeping.

### Added

- `CLAUDE.md` — the engineering constitution for this repository
- `docs/TECHNICAL_DEBT.md` — ranked debt register, each item with a trigger
- **Login throttle test coverage** (16 tests). The throttle previously had none,
  while a fixture docstring claimed otherwise. Runs against `fakeredis` so it
  executes everywhere rather than skipping without a Redis server
- **Authorization integration tests** (9 tests) covering each role, a token with
  no roles, a token with only unrecognised roles, and that authorization is
  decided before resource lookup so a 403 does not leak existence

### Changed

- Both `/api/v1/users` endpoints now require a recognised role via
  `RequireViewer`. `require_minimum_role` was previously unit-tested but wired
  to no endpoint
- Sidebar entries for unbuilt destinations render as disabled "Soon" items
  instead of linking to routes that returned 404
- Development branch renamed from `phase-0-foundation` to `develop`

### Fixed

- `frontend/tsconfig.tsbuildinfo` is no longer tracked in git
- Corrected a docstring in `tests/integration/conftest.py` that claimed test
  coverage which did not exist

---

## [phase-1-complete] — 2026-07-31

Authentication and multi-tenant identity. Commit `a21f7b8`.

### Added

**Authentication**
- Argon2id password hashing, with NFKC normalisation so the same password typed
  on different platforms verifies, and transparent rehashing when parameters are
  raised
- Password strength validation: minimum 12 characters, common-password denylist,
  and rejection of passwords containing the user's own email local part
- JWT access tokens (15 minutes) and refresh tokens (30 days), with the `typ`
  claim verified on every decode so a refresh token cannot be used as a bearer
  credential
- Refresh token rotation with reuse detection — replaying a consumed token
  terminates every session for that user
- `AuthenticatedUser` principal bound to context variables, feeding tenant
  filtering in every repository
- Role-based authorization: `require_roles` for exact membership and
  `require_minimum_role` for hierarchical checks
- Login throttling counted against email **and** client IP independently,
  checked before password verification
- Minimum length enforced on the JWT signing key (32 characters, RFC 7518 §3.2)
  in every environment

**API endpoints**
- `POST /api/v1/auth/register` — creates tenant, first user, and owner role
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout` — requires no access token
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/me` — roles read from the database, not the token

**Database** — migration `0002`
- `roles` table, seeded with four roles using deterministic UUIDv5 identifiers
  so a role has the same id in every environment
- `user_roles` association table with a composite primary key
- `refresh_tokens` table storing only a SHA-256 hash of each token

**Frontend**
- Sign-in, registration, and password-reset request pages
- `AuthProvider` session management; access token held in memory only, refresh
  token in an httpOnly path-scoped cookie
- Single-flight token refresh in the API client — without it, concurrent 401s
  each rotate the token, the second presents a consumed one, and reuse detection
  signs the user out
- `AuthGuard` route protection distinguishing *loading* from *unauthenticated*,
  so a page refresh does not eject an authenticated user
- Form primitives wiring react-hook-form and Zod to the design system with
  correct `aria-describedby` / `aria-invalid` handling

**Testing**
- 25 integration tests running against real PostgreSQL, building the schema by
  applying the Alembic migrations rather than `create_all`
- 53 new unit tests covering token verification, password handling, and
  authorization

**Documentation**
- `docs/Authentication.md`, `docs/Database.md`

### Changed

- **Tenant identity now comes from a verified JWT claim** instead of the
  `X-Tenant-ID` header. Confined to `app/api/deps.py`; no endpoint, service, or
  repository signature moved
- `users` gains `first_name`, `last_name`, `is_verified`
- `Base.__mapper_args__` sets `eager_defaults=True`, required in an async
  codebase — see Fixed below
- Enum columns pass `values_callable` so member values are persisted, not names
- `docs/DevelopmentSetup.md` rewritten; its `X-Tenant-ID` instructions had
  become actively wrong

### Removed

Each required by the Phase 1 specification. Safe because migration `0001` had
never been applied to any live database.

- `users.full_name` — replaced by `first_name` and `last_name`, with a read-only
  property preserving the display form
- `users.email_verified_at` — replaced by `is_verified`
- `users.role` enum column and the `user_role` type — replaced by `user_roles`.
  Keeping both would give a user's role two sources of truth, and reading the
  stale one is a privilege-escalation bug
- The Phase 0 `UserRole` **enum** was renamed to `RoleName` so the `UserRole`
  **table** could take the name

### Fixed

Three bugs found by running against real PostgreSQL. None was reachable without
a live database, and all three would have reached production.

- **Enum persistence.** SQLAlchemy sent member *names* (`"TRIAL"`) where the
  database expected values (`"trial"`), failing every tenant insert
- **`MissingGreenlet` on login.** Server-side `onupdate` columns are expired
  after a flush; serialising a just-updated user triggered lazy IO, which raises
  in async SQLAlchemy. This broke *every* login
- **No minimum JWT signing key length.** A short key signs and verifies
  normally and is only weaker, so the weakness was entirely silent

---

## [phase-0-complete] — 2026-07-30

Foundation. Commit `b513e4b`.

### Added

- Clean architecture with a strictly one-way dependency direction:
  `api → services → repositories → models`, with `core` depending on nothing
- Multi-tenancy enforced in a single base repository, reading the tenant from
  context so it cannot be forgotten at a call site
- UUID primary keys, UTC timestamps, soft deletes, deterministic constraint
  names, connection pooling
- Global exception handling producing one error envelope for every failure, with
  a request id on every response
- Structured logging with correlation across API and workers
- Redis caching with tenant-namespaced keys; distributed rate limiting with a
  circuit breaker
- Celery and RabbitMQ configured with context propagation and no jobs
- Next.js 15 / React 19 frontend, design system on CSS-variable tokens, light
  and dark themes
- Multi-stage non-root Dockerfiles, Compose stack, Nginx reverse proxy, GitHub
  Actions
- 54 tests, 11 of them targeting tenant isolation directly
- `README`, `Architecture`, `FolderStructure`, `CodingStandards`,
  `DevelopmentSetup`, `Contributing`

### Changed

- `app.workers.tasks` moved to `app.tasks` during finalisation. Tasks are entry
  points, architecturally symmetric with `api/`; `workers/` retains the
  infrastructure that runs them

### Fixed

- Pydantic `ClassVar` error that made the application unimportable
- Missing `email-validator` dependency
- Rate limiter paying a full Redis connect timeout on every request during an
  outage, adding ~4 seconds of latency per request

---

[Unreleased]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-2-complete...HEAD
[phase-2-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-1-complete...phase-2-complete
[phase-1-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-0-complete...phase-1-complete
[phase-0-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/releases/tag/phase-0-complete
