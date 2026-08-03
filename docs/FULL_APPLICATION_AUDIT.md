# Full application audit — DropPilot AI

| | |
|---|---|
| Date | 2026-08-02 |
| Branch audited | `develop` @ `ec958d2` (+ uncommitted WIP noted below) |
| `main` | Untouched (`82de677`) — verified |
| Scope | Backend, frontend, security, integrations, infra, AI (Phase 9) |
| Method | Static code review with file evidence + quality-gate spot checks |
| Code changes | **None this document.** Audit only, per brief. |

---

## 1. Baseline verification (this session)

| Check | Result | Notes |
|---|---|---|
| `pytest` (backend) | **740 passed** | Not a claim of production readiness |
| `npm run typecheck` | **Pass** | Frontend |
| `npm run lint` | **Pass** (prior session / explore) | Re-run before fix phase |
| Playwright full suite | **NOT VERIFIED** this session | Partial Stage 3 specs previously green |
| `docker compose up` local | **NOT VERIFIED** | C1 still open |
| Live AliExpress OAuth / business call | **NOT VERIFIED** | M10 / sandbox limits |
| Live Shopify Partner OAuth / Admin | **NOT VERIFIED** | M17 |
| Live AI provider call | **NOT VERIFIED** | M19 — StubProvider only |
| Working tree | **Dirty** | Uncommitted Shopify webhook/tunnel WIP (see A-20) |

Existing debt already tracked in [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) is **cross-referenced**, not duplicated as “new”, unless this audit adds evidence or severity change.

---

## 2. Summary counts

| Severity | New / re-validated this audit | Already in debt register |
|---|---|---|
| Critical | 1 | C1 (deployment unproven locally) |
| High | 5 | H4 (email verification off) |
| Medium | 12 | M4, M13, M15–M17, M19, … |
| Low | 8 | L1–L5, … |

Production readiness (honest): **~5.5/10** — strong architecture and test suite; open cross-tenant Shopify webhook routing, unproven Docker path, no live storefront/AI verification, and several UI/security hygiene gaps.

---

## 3. Critical

### A-01 — Shopify shop domain not globally unique; webhook routing can hit the wrong tenant

| | |
|---|---|
| **Severity** | Critical |
| **Status** | ✅ **RESOLVED** 2026-08-03 — migration `0012`, indexed webhook lookup, connect rejection |
| **Location** | `backend/app/models/shopify.py`; `backend/app/integrations/shopify/webhook.py`; migration `0008` / `0012` |
| **Evidence** | Uniqueness was per-tenant, not global. Webhooks resolved the shop by `list_connected(limit=1000)` then `next(row for row if shop_domain == …)` — first match wins. |
| **Impact** | If the same `*.myshopify.com` is connected under two tenants, a valid HMAC webhook can upsert orders into the **wrong** tenant (cross-tenant order/PII leak). Also fails silently for shops beyond the 1000-row scan window. |
| **Fix applied** | Global unique on `shop_domain`; `get_connected_by_shop_domain`; `ShopifyShopTakenError` on connect; webhook no longer scans. |
| **Verified** | pytest **745** passed including new A-01 unit/integration tests; ruff; mypy strict. |

---

## 4. High

### ~~A-02 — Logout does not clear React Query cache (comment lies)~~ ✅ RESOLVED

| | |
|---|---|
| **Severity** | High |
| **Location** | `frontend/providers/auth-provider.tsx` (`logout`, ~121–131) |
| **Evidence** | Comment claims React Query cache is discarded; implementation only `clearAccessToken` + `router.replace` + `router.refresh()` (Next RSC). **No** `queryClient.clear()`. `staleTime: 60_000` in query provider. |
| **Impact** | On a shared browser, User B can briefly see User A’s cached catalogue/orders until refetch — UI-level multi-tenant leak (API still auth-gated). |
| **Recommended fix** | Inject `QueryClient`; call `queryClient.clear()` on logout and `onTokenCleared`; optionally clear on login. Add Playwright/regression for cache wipe. |
| **Resolution** | `queryClient.clear()` on logout / token-cleared / login / register; Playwright probe in `shell.spec.ts`. |

### ~~A-03 — Tenant admin can mutate platform-global AI prompts~~ ✅ RESOLVED

| | |
|---|---|
| **Severity** | High |
| **Location** | `backend/app/repositories/ai_prompt.py` (unscoped); `backend/app/api/v1/ai/router.py` (`RequireAdmin`); documented in `docs/PHASE_9_PLAN.md` |
| **Impact** | Any tenant admin can change `product_title_generator` (etc.) for **all** tenants — sabotage, prompt injection, or brand damage at scale. |
| **Recommended fix** | Freeze write APIs until a platform-operator role exists; or tenant-scoped prompts with immutable platform defaults. |
| **Resolution** | `AI_ALLOW_PROMPT_MUTATION` defaults false; `PromptService` gates create / version / activate. Platform-operator role remains future work. |

### ~~A-04 — Shopify `publish_product` not Celery-idempotent~~ ✅ RESOLVED

| | |
|---|---|
| **Severity** | High |
| **Location** | `backend/app/tasks/integrations/shopify.py`; `backend/app/integrations/shopify/sync.py` (`POST /products.json` then persist listing); `celery_app` `task_acks_late` |
| **Impact** | At-least-once delivery after Shopify create / before listing persist → duplicate Shopify products. |
| **Recommended fix** | Idempotency: lookup by handle/SKU before create; or pending listing row before outbound call. |
| **Resolution** | Deterministic handle `droppilot-{product_id}`; lookup-before-create adopts an existing Shopify product on retry. |

### ~~A-05 — Nginx “same-origin” story broken by baked `NEXT_PUBLIC_API_URL`~~ ✅ RESOLVED

| | |
|---|---|
| **Severity** | High |
| **Location** | `docker-compose.yml` frontend build arg default `http://localhost:8000`; `frontend/lib/api-client.ts`; `nginx/conf.d/default.conf` comments |
| **Impact** | Browser via nginx `:80` still calls `:8000` → CORS/cookie failures; nginx `/api` proxy unused by SPA. |
| **Recommended fix** | Bake public origin (e.g. `http://localhost`) for compose; align `CORS_ORIGINS`; smoke-test the built client URL, not only nginx `/api`. |
| **Resolution** | Compose default `http://localhost`; CORS includes nginx origin; CI smoke rejects `:8000` in the frontend image. |

### A-06 — Playwright not in CI; rate-limit flakes on default env (M13) — FIX LANDED, CI UNVERIFIED

| | |
|---|---|
| **Severity** | High (reliability / release gate) |
| **Location** | `.github/workflows/ci.yml`; `.env.example`; `frontend/playwright.config.ts` |
| **Impact** | UI regressions never gated on push; full suite 429s on fresh clone. |
| **Fix landed** | `frontend-e2e` job + e2e ceiling docs + `start:e2e` standalone server. |
| **Verification gap** | Unit wiring tests passed locally; **the GitHub Actions `frontend-e2e` job has not been run in this pass** — leave open until that job is green on `develop`. |

### Existing High (not reopened)

| ID | Title | Status |
|---|---|---|
| **H4** | Email verification enforcement off | Still valid — foundation only |
| **C1** | Local Docker path unproven | Still Critical in debt register |

---

## 5. Medium

### A-07 — Integration status endpoints skip `RequireViewer`

| | |
|---|---|
| **Location** | `backend/app/api/v1/integrations/router.py` — `aliexpress_status`, `shopify_status` use `CurrentPrincipal` only |
| **Impact** | Empty/revoked-role token (within access TTL) can still read connection metadata / `last_error` / `appKey`. |
| **Fix** | Require `RequireViewer` (or admin) on both. |

### A-08 — Upstream / AI error strings returned to clients

| | |
|---|---|
| **Location** | Shopify `last_error=str(exc)`; `ProductOptimizationService` raises `AIError(failed.error_message)`; prompt execution stores `str(exc)[:2048]` |
| **Impact** | Internal/provider fragments can reach API consumers. |
| **Fix** | Stable error codes + short safe messages; raw detail logs/DB only. |

### ~~A-09 — Shopify webhook replay fails open into mutating upserts~~ ✅ RESOLVED

| | |
|---|---|
| **Location** | `backend/app/integrations/shopify/webhook.py` — on `RedisError`, continues processing |
| **Impact** | Redis outage → replayed webhooks re-upsert orders (load + any non-idempotent edges). AliExpress path is non-mutating by design — Shopify is not. |
| **Fix** | Fail closed (5xx) when replay store unavailable, or durable idempotency first. |
| **Resolution** | Fails closed (503) for mutating topics (`orders/create`, `orders/updated`, `app/uninstalled`) on a Redis error; non-mutating topics (`products/*`, `inventory_levels/*`) still acknowledge, since DropPilot never actually processes those today — a dedup failure there cannot cause a duplicate write. |

### A-10 — No production guard against default Postgres/RabbitMQ credentials

| | |
|---|---|
| **Location** | `app/core/config.py` defaults (`droppilot` / broker URL); Compose `:?` guards help Compose only |
| **Impact** | Deployed misconfig with defaults → weak DB/broker; Celery tasks take `tenant_id` args. |
| **Fix** | Refuse deployed boot on default passwords (same pattern as `SECURITY_SECRET_KEY`). |

### A-11 — Stale AliExpress webhook router docstring

| | |
|---|---|
| **Location** | `backend/app/api/v1/integrations/router.py` webhook summary still implies signature “not implemented” while HMAC opt-in exists |
| **Impact** | Operators misconfigure security posture. |
| **Fix** | Align docstring with Phase 7 dual-mode behaviour. |

### A-12 — Hand-written frontend API types (M4)

| | |
|---|---|
| **Location** | `frontend/types/api.ts` + service-local interfaces |
| **Impact** | Silent drift vs OpenAPI. |
| **Fix** | `openapi-typescript` from `/openapi.json`. |

### ~~A-13 — `next start` vs `output: "standalone"` mismatch for Playwright~~ ✅ RESOLVED (with A-06)

| | |
|---|---|
| **Location** | `frontend/next.config.ts`, `playwright.config.ts` (`npm run start`) |
| **Impact** | Local e2e production path ≠ Docker `node server.js`. |
| **Fix** | Point Playwright at standalone server or dual config. |
| **Resolution** | `npm run start:e2e` → `scripts/start-standalone.mjs`. |

### A-14 — Stale `docs/Frontend.md` still claims mock dashboard data

| | |
|---|---|
| **Location** | `docs/Frontend.md` |
| **Evidence** | Dashboard uses live `/analytics`; `lib/mock/` empty |
| **Impact** | False debugging assumptions. |
| **Fix** | Rewrite to Phase 6+ reality. |

### A-15 — Silent / weak error UX on ops surfaces — partially resolved

| | |
|---|---|
| **Location** | `order-statistics-cards.tsx` (`isError → null`); `pricing-actions.tsx` / `sync-inventory-button.tsx` (errors muted); ~~`shopify-card.tsx` disconnect no catch~~ |
| **Impact** | Failures look like empty success. |
| **Fix** | ErrorState / destructive Alert; surface disconnect errors. |
| **Resolution (Shopify only)** | `shopify-card.tsx` now wraps disconnect in try/catch and renders a per-store error message; the button no longer fails silently. `order-statistics-cards.tsx`, `pricing-actions.tsx`, and `sync-inventory-button.tsx` are unchanged — still open. |

### ~~A-16 — Uncommitted Shopify webhook tunnel workaround on working tree~~ ✅ RESOLVED

| | |
|---|---|
| **Location** | Was dirty: `integrations/router.py` (POST `/shopify/callback`), `shopify/service.py` (`webhook_delivery_address`), tests, SHOPIFY docs, `.env.example` |
| **Evidence** | Present in working tree; **absent from `HEAD`** (`git show HEAD:…` empty for these symbols) |
| **Impact** | Path-scoped Cloudflare tunnels cannot deliver Shopify webhooks on clean `develop` tip; local-only fix can be lost. |
| **Fix** | Finish review, commit as intentional `fix(shopify): …`, or discard if superseded. Do not leave indefinitely. |
| **Resolution** | The WIP was actually broken — `webhook_delivery_address()` was referenced by a test but never defined, so the module failed to collect. Implemented it, committed as `fix(shopify): finish webhook tunnel workaround, add missing topics, fail closed on replay outage (A-16, A-09)`. |

### A-17 — Celery / broker end-to-end still not proven locally (M15)

| | |
|---|---|
| **Location** | Debt M15; CI broker job exists |
| **Impact** | Task behaviour under real RabbitMQ not observed on this machine. |
| **Fix** | Confirm CI green; local broker when Docker available. |

### A-18 — Live Shopify / AliExpress / AI gaps (M17 / M10 / M19)

| | |
|---|---|
| **Impact** | Cannot claim production storefront or AI copy quality. |
| **Fix** | Partner credentials + one live OAuth; AI key + one live fixture; keep StubProvider for CI. |

---

## 6. Low

| ID | Issue | Location | Fix sketch |
|---|---|---|---|
| A-19 | JWT roles not re-checked on hot path (TTL window) | `deps.py`, `tokens.py` | Short TTL or re-check for high-risk mutations |
| A-20 | Forgot-password pretends availability until submit | `forgot-password/page.tsx` | Banner upfront |
| A-21 | Customers ComingSoon vs nav `coming-soon` inconsistency | `navigation.ts`, `/customers` | Align IA |
| A-22 | Misleading “Zustand unused” comment | `notification-menu.tsx` | Delete comment |
| A-23 | `.env.example` `SECURITY_COOKIE_SECURE=true` vs local HTTP guide | `.env.example`, deployment docs | Align for `ENVIRONMENT=local` |
| A-24 | AliExpress webhook unsigned by default (non-mutating today) | webhook security dual-mode | Require secret before any mutation |
| A-25 | Offset pagination / ILIKE search (L1/L2) | Debt register | Revisit at catalogue scale |
| A-26 | Dashboard Recharts bundle size (M8) | Frontend.md / debt | Code-split charts |

---

## 7. Area scorecards

### Backend architecture

| Area | Assessment |
|---|---|
| Layering `api → services → repositories → models` | **Sound** — spot-checked products, AI, Shopify |
| TenantScopedRepository | **Sound** — isolation tests present; Shopify webhook resolution is the outlier |
| Auth (Argon2id, JWT `typ`, refresh rotation) | **Sound** — S1–S5 closed |
| Error envelope | **Sound** for unhandled/SQLAlchemy; **weak** for `str(exc)` on some integration paths |
| Celery | Configured correctly in code; publish idempotency **mitigated** (A-04); broker path CI-only (M15) |

### Frontend architecture

| Area | Assessment |
|---|---|
| Fetch via `services/` | **Sound** |
| React Query vs Zustand | **Sound** (UI-only Zustand) |
| Mock data as truth | **Gone** from code; docs stale (A-14) |
| Auth cache hygiene | **Fixed** (A-02) |
| Type safety | No `any`; hand-written types (A-12) |

### AI (Phase 9 stages 1–3)

| Area | Assessment |
|---|---|
| Provider abstraction + StubProvider | **Sound** |
| Prompt render → execute → record | **Sound** under stub |
| Product versions / supplier overwrite protection | **Sound** by construction |
| Prompt admin privilege | **Mitigated** (A-03 — env gate; operator role still future) |
| Live model quality | **NOT VERIFIED** (M19) |

### AliExpress

| Area | Assessment |
|---|---|
| OAuth + Fernet tokens + client signing | Architecture sound |
| Platform credentials (migration 0009) | Shipped |
| Live `AliExpressClient.call()` on this machine | **NOT VERIFIED** this session |
| Webhook unsigned default | Acceptable while non-mutating; document (A-24) |

### Shopify

| Area | Assessment |
|---|---|
| OAuth HMAC / state | Architecture sound |
| Live Partner install | **NOT VERIFIED** (M17) |
| Webhook HMAC | Required when secret set |
| Tenant resolution | **Critical defect** (A-01) |
| Tunnel webhook delivery on clean tip | **Fixed** (A-16 committed) |
| Webhook replay/dedup on Redis outage | **Fails closed for mutating topics** (A-09) |
| `app/uninstalled` handling | Registered and marks the connection `ERROR` immediately (previously not handled at all) |

### Infrastructure

| Area | Assessment |
|---|---|
| Compose secret defaults removed | Resolved (M18) |
| CI compose smoke blocking | Present — not re-run here |
| Local Docker | **NOT VERIFIED** (C1) |
| Nginx + SPA API URL | **Aligned** (A-05 — Compose defaults to nginx origin) |

---

## 8. Security checklist (this audit)

| Control | Status |
|---|---|
| Secrets in `NEXT_PUBLIC_*` | Pass (none found) |
| JWT typ / alg / iss / aud | Pass |
| Password hashing Argon2id | Pass |
| Fernet encryption startup guards | Pass (S1–S4) |
| Log redaction | Pass (S5) |
| Cross-tenant Shopify webhook | **Fail** (A-01) |
| Prompt privilege boundary | **Pass (gated)** (A-03 — `AI_ALLOW_PROMPT_MUTATION`) |
| Logout cache wipe | **Pass** (A-02) |
| CSRF on cookie refresh | Cookie + CORS model — **NOT fully re-proven** in browser this session |
| Webhook HMAC Shopify | Pass when configured |
| Compose password defaults | Pass in Compose; app-level defaults still Medium (A-10) |

---

## 9. Recommended fix order (for Phase 3 of the brief)

Do **not** start until this report is accepted. Suggested sequence:

1. **A-01** — Global Shopify `shop_domain` uniqueness + webhook lookup (security)
2. ~~**A-02** — React Query clear on logout (security / tenancy UI)~~ ✅
3. ~~**A-03** — Lock AI prompt writes or introduce platform operator~~ ✅
4. **A-07 / A-08** — Status authz, safe errors / ~~**A-09** — Shopify replay fail-closed~~ ✅
5. ~~**A-04** — Publish idempotency~~ ✅
6. ~~**A-16** — Commit or discard Shopify tunnel webhook WIP~~ ✅
7. ~~**A-05**~~ ✅ / **A-06** — Compose API URL + CI Playwright (fix landed; CI unverified)
8. Medium/Low UX and docs (A-14, ~~A-15 (Shopify part)~~ ✅ / A-15 (remaining) …)

For every fix: root-cause note, tests, re-run quality gates. Do not remove tests to go green.

---

## 10. Explicitly NOT VERIFIED

- Full Playwright suite (all projects) this session  
- `docker compose up --build` on this host  
- Live AliExpress connect + product import against gateway  
- Live Shopify Partner OAuth + Admin API  
- Live AI provider HTTP call  
- CI `compose-smoke` job green on latest push (not polled here)  
- Exhaustive OpenAPI ↔ `types/api.ts` field diff  
- Production `.env` contents (must never be committed)

---

## 11. What looked solid

- One-way dependency rule generally respected  
- TenantScopedRepository + 404-not-403 cross-tenant policy on product optimization  
- Auth audit S1–S5 + Compose secret guard M18  
- Phase 9 StubProvider honesty (no fake live AI)  
- Backend suite **740** green; frontend typecheck green  
- No frontend `any`; no Zustand holding API responses  

---

**Next step (human):** Review this report. Reply to proceed to **Phase 3 — Fix issues** in priority order, or to narrow the fix set.
