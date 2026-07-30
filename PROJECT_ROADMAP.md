# Project roadmap

Development status for DropPilot AI. Each phase is a defined scope delivered in
full before the next begins.

Last updated: 2026-07-31

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ **Complete** |
| 1 | Authentication and multi-tenant identity | ✅ **Complete** |
| 2 | *Scope not yet defined* | 🚧 **In Progress** |

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

## Phase 2 — 🚧 In Progress

**Scope is not yet defined.** Marked in progress at the point Phase 1 was
finalised; the requirements arrive with the Phase 2 prompt and this entry will
be filled in then.

No Phase 2 code has been written.

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
each phase boundary. As of `phase-1-complete`: 1 critical, 4 high, 6 medium,
5 low.

The critical item is that the **deployment path has never been executed** —
Docker images, Compose, and Nginx are all unbuilt.

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
