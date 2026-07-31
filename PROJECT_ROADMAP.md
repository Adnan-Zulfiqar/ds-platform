# Project roadmap

Development status for DropPilot AI. Each phase is a defined scope delivered in
full before the next begins.

Last updated: 2026-07-31

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ **Complete** |
| 1 | Authentication and multi-tenant identity | ✅ **Complete** |
| 2 | Application shell, navigation, and SaaS UI foundation | ✅ **Complete** |
| 3 | AliExpress integration foundation | ✅ **Complete** |
| 4 | Product import and catalogue synchronisation | ✅ **Complete** |

---

## Phase 0 — Foundation ✅

**Tag:** `phase-0-complete` · **Commit:** `b513e4b`

Established the architecture with no business features. Clean architecture with
a one-way dependency direction; multi-tenancy enforced in a single base
repository; UUID keys, UTC timestamps, soft deletes; global exception handling
producing one error envelope; structured logging with request correlation;
Redis caching and distributed rate limiting; Celery and RabbitMQ configured with
no jobs; Next.js frontend with a design system and dark mode; Docker, Nginx, and
CI.

Full report: [PHASE_0_COMPLETION.md](docs/PHASE_0_COMPLETION.md)

---

## Phase 1 — Authentication and multi-tenant identity ✅

**Tag:** `phase-1-complete` · **Commit:** `a21f7b8`

Replaced the Phase 0 `X-Tenant-ID` header with JWT-verified identity. The change
was confined to `app/api/deps.py` — no endpoint, service, or repository
signature moved, because they already depended on bound context rather than the
header.

Argon2id password hashing; JWT access and refresh tokens with the `typ` claim
verified; refresh rotation with reuse detection; `AuthenticatedUser` principal;
`Role`/`UserRole`/`RefreshToken` tables and migration `0002`; role-based
authorization dependencies; login throttling on email and IP; six auth
endpoints; login, register, and forgot-password pages with in-memory access
tokens and an httpOnly refresh cookie.

Full detail: [Authentication.md](docs/Authentication.md)

---

## Phase 2 — Application shell and SaaS UI foundation ✅

**Tag:** `phase-2-complete` · **Commit:** `1839869`

The professional interface foundation every future module builds on. Frontend
only; no business functionality.

Collapsible sidebar with six navigation sections driven by a single manifest;
responsive drawer below `md`; top bar with notification centre, theme toggle,
and a user menu showing identity, tenant, and role. Dashboard with six stat
cards and three reusable charts on clearly-quarantined mock data. Nine new
design-system primitives. Six protected routes plus unauthorized, loading, and
error states.

Verified with **47 Playwright tests against a real backend** — accounts are
registered through the API rather than stubbed — at 320px, 768px, and 1440px in
both themes.

Full detail: [Frontend.md](docs/Frontend.md)

---

## Phase 3 — AliExpress integration foundation ✅

The connection foundation for supplier automation. No product, price, inventory,
or order functionality — those are later phases, and this exists so they are
feature work rather than infrastructure work.

Fernet credential encryption with key rotation; `aliexpress_connections` and
migration `0003`; a signed HTTP client with timeouts, jittered retries, error
mapping, and outbound rate limiting; the OAuth flow with a single-use
server-side `state` token; four endpoints; a Celery health check; and a real
integrations settings page.

Closed with sub-phases 3.5–3.7: live OAuth verification, permission
verification, and an inbound webhook at
`POST /api/v1/integrations/aliexpress/webhook`.

Verified with **291 backend tests** and **116 Playwright tests** (114 passed,
2 flaky, 0 failed). Migration `0003` applied against PostgreSQL 17.

**Verified against the real AliExpress API — partially.** A live OAuth round
trip completes and stores encrypted tokens; product, category, search, order and
freight endpoints are confirmed reachable; affiliate is confirmed denied. Two
defects were found this way and could not have been found any other way: the
signature omitted `sign_method`, and the callback required a Bearer token no
browser redirect carries.

**Still unverified:** no business API call has been made through
`AliExpressClient`, and no response schema has parsed a real payload. That is
the main risk carried into Phase 4.

Full detail: [PHASE_3_COMPLETION.md](docs/PHASE_3_COMPLETION.md),
[PHASE_3_6_COMPLETION.md](docs/PHASE_3_6_COMPLETION.md),
[PHASE_3_7_PERMISSIONS.md](docs/PHASE_3_7_PERMISSIONS.md).

---

## Phase 4 — Product import and catalogue synchronisation ✅

**Tag:** `phase-4-complete`

Import products from AliExpress by supplier product id, store variants and
images per tenant, refresh price and stock on demand, and expose a products UI
backed by the real API.

Contract schemas built from live captured payloads; migration `0004`; tenant-scoped
repositories with SQL compile isolation tests; idempotent import service reusing
the Phase 3 client; six product endpoints; Celery sync foundation; Playwright
coverage for UI-owned paths with documented skips when live OAuth cannot complete.

Verified with **419 backend tests** and frontend lint, typecheck, and build.
Playwright import-flow tests skip on a developer backend where the live gateway
rejects the synthetic OAuth code — parsing and storage are covered by integration
tests against captured fixtures.

Full report: [PHASE_4_COMPLETION.md](docs/PHASE_4_COMPLETION.md),
[PHASE_4_PLAN.md](docs/PHASE_4_PLAN.md).

---

## Phase 5 — ⏳ Not started

Scope arrives with the Phase 5 prompt. The catalogue foundation is ready to
receive store mapping, bulk import, or order workflow — whichever the next
phase specifies.

---

## Later phases

Not scheduled, and listed only so that architectural seams are built with them
in mind. Nothing here is committed to a phase number.

| Area | Notes |
|---|---|
| Store connections | Shopify, WooCommerce, eBay, Etsy, TikTok Shop, AliExpress. Third-party credentials must be encrypted at rest with a key held outside the database |
| Product import and editing | The platform's highest-volume entity; drives the keyset-pagination and full-text-search decisions |
| AI optimisation | Listing content generation and enhancement |
| Inventory and price sync | Background work; the reason queue separation is configured but unused |
| Order fulfilment and tracking | Money and personal data; needs an audit trail and must never hard-delete |
| Analytics | Read replica or rollup tables rather than aggregating over live tables |
| Subscription billing | Plan limits attach to `tenants` |
| Team management | Invitations; the `UserCreate` schema already exists for it |
| Admin panel | Platform operations across tenants |
| Notifications and reporting | Email and webhook delivery |

---

## Technical debt

Tracked separately in [TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md), reviewed at
each phase boundary.

A housekeeping pass on 2026-07-31 resolved all three actionable High items —
login throttle coverage, authorization wiring, and dead navigation links.

**Current: 1 critical, 1 high, 12 medium, 5 low.**

The critical item is that the **deployment path has never been executed** —
Docker images, Compose, and Nginx are all unbuilt. It cannot be closed on this
machine (no Docker); the CI `docker` job builds all three images, so opening a
pull request retires it.

The remaining High item (`is_verified` unenforced) is inert until email
verification is implemented, and is best fixed in that phase.

## Deferred technical decisions

Recorded so they are chosen deliberately rather than by accident. Each has a
trigger rather than a date.

| Decision | Revisit when |
|---|---|
| Password reset and email verification | Requires mail delivery — the largest gap in Phase 1 |
| Breached-password corpus check | Before live customer accounts exist |
| Multi-factor authentication | Customer or compliance requirement |
| RS256 token signing | A separate auth service or verification-only party appears |
| Generated API types from OpenAPI | Before the API surface grows past a handful of endpoints |
| Keyset pagination | Catalogues approaching millions per tenant |
| Full-text search (`tsvector` + GIN) | When product search becomes a primary workflow |
| Read replica for analytics | When reporting contends with transactional load |
| PgBouncer | When replicas × pool size approaches `max_connections` |
| Domain event bus | When a second subscriber to a state change appears |
