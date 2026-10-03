# Project roadmap

Development status for DropPilot AI. Each phase is a defined scope delivered in
full before the next begins.

Last updated: 2026-09-21

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ **Complete** |
| 1 | Authentication and multi-tenant identity | ✅ **Complete** |
| 2 | Application shell, navigation, and SaaS UI foundation | ✅ **Complete** |
| 3 | AliExpress integration foundation | ✅ **Complete** |
| 4 | Product import and catalogue synchronisation | ✅ **Complete** |
| 5 | Order management, fulfilment & synchronisation | ✅ **Complete** |
| 6 | Inventory, pricing, multi-store, automation & tracking | ✅ **Complete** |
| 7 | Production hardening & operational readiness | ✅ **Complete** |
| 8 | Shopify sales-channel integration | ✅ **Complete** |
| 8.1 | Shopify OAuth production quality | ✅ **Complete** (live Partner OAuth still M17) |
| 9 | AI product optimization | 🚧 **In progress** — stages 1–9 merged into `develop` (Stage 9: PR #24, merge `72e7692`, post-merge CI 35656740281 10/10). Claude return review of Stages 5–9 done 2026-09-30; its remediation is on `fix/stage5-9-review-remediation` (unmerged, awaiting independent review — see [REVIEW_REMEDIATION_STAGE_5_9.md](docs/REVIEW_REMEDIATION_STAGE_5_9.md)). Remediation merged (PR #26, `2b71f65`). Stage 10 plan merged (PR #25, `81f952f`); Stage 10 merged (PR #36, `fbadddb`; CI 10/10). Stage 11 report: [PHASE_9_COMPLETION.md](docs/PHASE_9_COMPLETION.md); Cursor independent review of `e63508e` (2026-10-02): implementation ACCEPTED WITH NON-BLOCKING NOTES, release NOT READY ([report](docs/reviews/cursor/INDEPENDENT_REVIEW_e63508e.md)); tag `phase-9-complete` stays uncreated. `StubProvider` in all recorded evidence; `OpenAIProvider` added after Stage 11 (mocked-transport tests only, no real call yet). Production undeployed |
| UX-L2A | Product editor foundation | ✅ **Integrated into `develop` (`e1dd0f0`)** — production undeployed |
| UX-L2B | Server-authoritative publish integrity | ✅ **Present in `develop` (`a543b029` = tip of `feature/ux-l2b-publish-integrity`, R7)** — production undeployed. Earlier "not merged" wording predates the fast-forward |
| Phase 1 (Git/CI) | CI baseline repair and PR #7 closure | ✅ **Merged into `develop`** — PR #7 merged 2026-09-16, merge commit `5e21927`. Accepted SHA `c0092da6`: independent review `PASS — PHASE 1 MAY CLOSE` (2026-09-15), CI run 34846601320 10/10; CI on the merge commit run 35164379004 10/10. **Production undeployed** — `main` unchanged. Celery exclusive-pidbox multi-worker/restart drill remains a separate safe-staging validation item |
| UX-L2C (historical) | Live-state clarity and calm completion journey | 🔍 **`origin/feature/ux-l2c-live-state-clarity` (`ece8322`)** — unmerged; reviewed under UX-L2D-GATE-04 (cleared 2026-09-15): selected pieces adapted into UX-L2D-04 (product page, lifecycle subset, external-link allowlist, listings query options, route-isolation spec); the rest deferred to -05/-06 or superseded |
| UX-L2D | Dashboard design programme (renamed from UX-L2C) | ✅ **Merged into `develop`** — PR #9 merged 2026-09-17, merge commit `b52b223`. Original frozen review SHA `3f1ede8` (tree `0aac7f36`, independent review PASS); final accepted head `41c152c` (tree `489366a9`; two remediation commits, remediation delta review PASS); 27 commits stacked on accepted Phase 1 `c0092da6`. CI on `41c152c` run 35109481727 and on `b52b223` run 35191388324, both 10/10 — Playwright 722 passed / 0 failed / 9 skipped. No backend/CI files changed; Vitest still local only (not a CI gate). **Production undeployed** — `main` unchanged. Integration record and backend dependencies: `docs/ux/ux-l2d-phase-2-status.md` |

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

## Phase 5 — Order management, fulfilment & synchronisation ✅

**Tag:** `phase-5-complete`

Orders domain with migration `0005`; idempotent AliExpress order sync; five
API endpoints; Celery sweep/refresh/cleanup with beat entries; shipment
tracking and a validated fulfilment lifecycle; webhook replay protection
(unsigned payloads still never mutate state directly); Orders UI with detail,
timeline, and live statistics; dashboard order-synchronisation row backed by
the real statistics endpoint.

Live verification through `AliExpressClient.call()` confirmed request signing,
building, and error mapping — including the live finding that failures arrive
wrapped in an `error_response` envelope. A populated order-detail success body
was not available on the sandbox account (debt M16).

Verified with **523 backend tests** and frontend lint, typecheck, and build.
Orders Playwright suite: 16 passed. Full Playwright suite remains subject to
M13 parallelism flakes.

Full report: [PHASE_5_COMPLETION.md](docs/PHASE_5_COMPLETION.md).

---

## Phase 6 — Inventory, pricing, multi-store, automation & tracking ✅

**Tag:** `phase-6-complete`

Operations platform on top of Phase 4/5: sales-channel stores; inventory sync
reusing product import; dynamic pricing with preview/apply audit; automation
rules that dispatch to existing services; notification centre; real analytics
dashboard (mock data removed); shipment tracking list + TrackingEvent writes;
Celery tasks for inventory/pricing/automation/shipments/analytics/cleanup.

Live `AliExpressClient.call()` verified for the inventory dependency
(`product.get`). No new AliExpress methods were added.

Verified with **566 backend tests** and frontend lint, typecheck, and build.
Full report: [PHASE_6_COMPLETION.md](docs/PHASE_6_COMPLETION.md).

---

## Phase 7 — Production hardening & operational readiness ✅

**Tag:** `phase-7-complete`

CI-ready deployment validation (develop triggers, image builds, compose config,
Celery broker job, best-effort compose smoke); Compose beat + worker health;
webhook HMAC opt-in + shed limiter; email-verification foundation without fake
SMTP; store-channel decision (remain manual; Shopify first later); frontend
cleanup.

Local Docker and local RabbitMQ were unavailable; C1/M15 local runtime legs are
documented as CI-dependent. Live AliExpress re-check skipped (no connected
account after earlier DB rebuild).

Full report: [PHASE_7_COMPLETION.md](docs/PHASE_7_COMPLETION.md).
Decision: [STORE_CHANNEL_DECISION.md](docs/STORE_CHANNEL_DECISION.md).

---

## Phase 8 — Shopify sales-channel integration ✅

**Tag:** `phase-8-complete`

Implements Option A from the store-channel decision: Shopify OAuth, encrypted
tokens, product publish (idempotent listings), inventory/price push, order
import foundation, HMAC webhooks, Celery tasks, and Integrations UI.

Live Shopify Partner OAuth was **not** available on the development machine —
unit/integration coverage uses mocks where needed; see
[PHASE_8_COMPLETION.md](docs/PHASE_8_COMPLETION.md) and
[SHOPIFY_INTEGRATION.md](docs/SHOPIFY_INTEGRATION.md).

**Audit fix pass (2026-08-03).** A full-application audit
([FULL_APPLICATION_AUDIT.md](docs/FULL_APPLICATION_AUDIT.md)) found and closed
three Shopify-specific gaps without changing the OAuth architecture itself:
an uncommitted webhook-tunnel workaround that was actually broken (A-16), two
missing webhook topics plus `app/uninstalled` handling, webhook replay-dedup
failing open on a Redis outage for mutating topics (A-09), and a disconnect
button with no error handling (A-15, Shopify surface only). Full write-up:
[SHOPIFY_OAUTH_IMPLEMENTATION.md](docs/SHOPIFY_OAUTH_IMPLEMENTATION.md). Live
Shopify Partner OAuth verification remains open (M17) — unchanged by this pass.

---

## Phase 8.1 — Shopify OAuth production quality ✅

**Tag:** `phase-8-1-complete` (implementation tip; **not** live-OAuth verified)

Brings Shopify install to AutoDS-like production quality without asking merchants
for API keys or tokens:

- `GET /api/v1/integrations/shopify/install` — App URL entry (HMAC → authorize or claim)
- `POST /shopify/claim-install` — bind anonymous App URL installs to a workspace
- Typed Connect remains domain-only with a dialog UX (skippable when shop is known)
- Webhook base validation (`/webhooks`, `/callback`, or singular `/webhook`)
- Idempotent webhook registration; uninstall releases the global shop claim
- Disconnect best-effort revokes Shopify access; reconnect reuses the store row

**Follow-up verification** ([PHASE_8_1_VERIFICATION.md](docs/PHASE_8_1_VERIFICATION.md)):
frontend build + Playwright Shopify/integrations/shell green; production
`/install` still 404; live Partner consent still M17. The original tag was not
moved.

Full report: [PHASE_8_1_COMPLETION.md](docs/PHASE_8_1_COMPLETION.md).

---

## Product Workspace V2 / Draft Editor (in progress)

Draft-to-Product lifecycle so AliExpress imports land in **Drafts** and only
appear under **Products** after a successful channel publish (`StoreListing`
synced). Plans:
[PRODUCT_WORKSPACE_V2_PLAN.md](docs/PRODUCT_WORKSPACE_V2_PLAN.md),
[DRAFT_PRODUCT_EDITOR_PLAN.md](docs/DRAFT_PRODUCT_EDITOR_PLAN.md).

| Stage | Scope | Status |
|---|---|---|
| 0 | Publication query split, Drafts nav/list, import history | **Done on `develop`** |
| 1 | Clickable draft rows + Edit / Publish actions | **Done on `develop`** |
| 2 | Draft write APIs (reuse PE `ProductService`) | **Done on `develop`** |
| 3 | Editor shell + Overview / Description / SEO / Publish panel | **Done (MVP) on `develop`** |
| 4 | Media + structured variants | **Done (MVP) on `develop`** |
| 5 | Pricing / inventory / shipping | **Done (MVP) on `cursor/draft-product-editor`** |
| 6–8 | AI Studio, publish polish, E2E | Pending |
| UX-L2A | Compact command bar, grouped nav, shipping/checklist copy | **Integrated into `develop` (`e1dd0f0`); production undeployed** |
| UX-L2B | Save-before-publish integrity + server publish authority | **Present in `develop` at `a543b029` (R7 tip fast-forwarded); production undeployed.** Historical UX-L2C follows on `feature/ux-l2c-live-state-clarity` (unmerged); the new dashboard programme is UX-L2D |
| UX-L2D | Dashboard programme: shell, Home, catalogue, editor lifecycle, channels, hardening | **Merged into `develop` at `b52b223` (PR #9, 2026-09-17; accepted head `41c152c`); production undeployed.** Evidence in the Status table above |

Product Editor Stages 1–2b (description sanitize, PATCH product, sync identity)
already shipped on develop and are not recreated here.

---

## Later phases

Not scheduled, and listed only so that architectural seams are built with them
in mind. Nothing here is committed to a phase number.

| Area | Notes |
|---|---|
| Additional store channels | WooCommerce / Etsy / TikTok. Shopify shipped in Phase 8; **eBay is connected** as of EBAY-C1, EBAY-C2 adds listing setup (policies, locations, defaults) and EBAY-C3 publishes a single-variant draft as a fixed-price listing (mocked eBay transport only; C4–C6 follow) — see [docs/ebay/MASTER_EBAY_ROADMAP.md](docs/ebay/MASTER_EBAY_ROADMAP.md) |
| Shopify fulfilment push | ✅ Track E1 on `develop`: merchant marks a Shopify order shipped with tracking (`fulfillmentCreate`); new fulfilment scopes need a reconnect; faked-client tests only — [E1 doc](docs/track-e/E1_SHOPIFY_FULFILMENT.md) |
| AI optimisation (Phase 9) | Stages 1–9 merged (Stage 9: PR #24, merge `72e7692`; post-merge CI 35656740281 10/10). Review remediation merged (PR #26). Stage 10 merged (PR #36). Stage 11 report written; implementation accepted with notes by Cursor, release NOT READY; untagged. `StubProvider` only — no live model-quality verification. Production undeployed |
| Product Workspace V2 | Stage 0 done; Stages 1–8 remaining (see above) |
| Real FX for pricing | **M24A partial:** Open Exchange Rates + Shopify GraphQL currency authority. Remaining: M24B (AliExpress ship-to/GBP mapping), M24C (fees/tax/landed cost). Not production pricing-ready. |
| Subscription billing | Plan limits attach to `tenants` |
| Team management | Invitations; the `UserCreate` schema already exists for it |
| Admin panel | Platform operations across tenants |
| Email notifications | In-app centre exists; outbound email delivery does not |

---

## Technical debt

Tracked separately in [TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md), reviewed at
each phase boundary.

A housekeeping pass on 2026-07-31 resolved all three actionable High items —
login throttle coverage, authorization wiring, and dead navigation links.

**Current: see [TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md)** after Phase 7
updates. C1 is narrowed (CI path ready; local Docker still absent). H4 is
narrowed to “enforcement off until SMTP”. M11/M12 dual-mode mitigations landed;
mutation from unsigned webhooks remains forbidden. M15 moves to CI broker job
confirmation after push.

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
