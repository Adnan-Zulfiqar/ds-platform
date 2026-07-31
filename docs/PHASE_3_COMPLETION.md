# Phase 3 — completion report

**AliExpress Developer API Integration Foundation**, including the live
verification sub-phases 3.5, 3.6 and 3.7 and the inbound webhook.

Everything below was re-verified from scratch for this release. Where something
has never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Tag | `phase-3-complete` |
| Gateway verified | `api-sg.aliexpress.com` |
| Application | app key `541272`, environment `test` |

---

## 1. Architecture summary

```
app/integrations/aliexpress/
├── exceptions.py   typed failures, each declaring whether it is retryable
├── schemas.py      wire models (theirs) and API models (ours)
├── auth.py         request signing + OAuth state
├── webhook.py      inbound push notifications (separate from OAuth callback)
├── client.py       the only code that talks to AliExpress over the network
└── service.py      connection lifecycle; knows nothing about HTTP
```

Dependency flow is unchanged and one-way: `api → services → repositories →
models`, with `core` at the bottom. The integration package is reachable from a
router, a Celery task or a CLI without modification, because nothing in it
imports `app.api` and nothing raises `HTTPException`.

The router remains thin. Both AliExpress-specific handlers — the OAuth callback
and the webhook — delegate immediately; neither contains signing logic, error
vocabulary or token handling.

## 2. Implementation summary

| Capability | Where |
|---|---|
| Credential encryption (Fernet, MultiFernet rotation) | `core/encryption.py` |
| Connection model and migration 0003 | `models/integration.py` |
| Request signing (HMAC-SHA256, sorted, path-prefixed) | `integrations/aliexpress/auth.py` |
| OAuth state (256-bit, Redis, single-use) | `integrations/aliexpress/service.py` |
| HTTP client with typed errors and retry | `integrations/aliexpress/client.py` |
| Connection lifecycle | `integrations/aliexpress/service.py` |
| Inbound webhook | `integrations/aliexpress/webhook.py` |
| Endpoints | `api/v1/integrations/router.py` |
| Outbound rate limiting (fails closed) | `integrations/rate_limiter.py` |
| Settings UI | `frontend/components/integrations/aliexpress-card.tsx` |

## 3. Files changed in this release

| File | Change |
|---|---|
| `backend/app/integrations/aliexpress/webhook.py` | **New.** Inbound webhook parsing and acknowledgement |
| `backend/app/integrations/aliexpress/schemas.py` | Added `AliExpressWebhookAckResponse`; removed a dead duplicate |
| `backend/app/api/v1/integrations/router.py` | Added `POST /aliexpress/webhook` |
| `backend/app/middleware/rate_limit.py` | Webhook exempted, with the trade-off documented inline |
| `backend/tests/unit/test_aliexpress_webhook.py` | **New.** 12 unit tests |
| `backend/tests/integration/test_integrations.py` | Webhook integration tests |
| `frontend/components/integrations/aliexpress-card.tsx` | Credentials optional (platform application by default) |
| `frontend/types/api.ts` | `appKey` / `appSecret` optional |
| `frontend/tests/e2e/integrations.spec.ts` | Fixed an unsafe assertion that read a navigated-away response body |
| `docs/ALIEXPRESS_INTEGRATION.md` | Webhook flow section |
| `docs/TECHNICAL_DEBT.md` | M10 narrowed; M11–M14 added |

Earlier in Phase 3, committed separately: the `.env` loading fix
(`ba752c9`), the callback move to `api.whiteto.com` (`3a46329`), the OAuth live
fixes (`74c6653`), the signature fix (`fa02dd2`), and the two verification
reports (`3c07488`, `a71c5ae`).

## 4. Live verification results

36 of 36 checks passed against the running application, the live gateway,
PostgreSQL and Redis. Re-run from scratch for this release.

| Group | Result |
|---|---|
| OAuth lifecycle | 13/13 |
| Authorization boundaries | 3/3 |
| Webhook contract | 7/7 |
| Live AliExpress API calls | 6/6 |
| Encryption at rest | 5/5 |
| Tenant isolation | 2/2 |

Specifically confirmed: state stored in Redis with a bounded TTL and bound to a
tenant server-side; **state deleted after one use and a replay rejected**;
callback answers 303 and never 401; disconnect idempotent; reconnect after
disconnect works; connect and status both reject unauthenticated callers; every
stored credential is a Fernet envelope that decrypts and contains no plaintext;
11 connections across 11 distinct tenants with no tenant holding two.

### Live API verification

| Capability | Method | Result |
|---|---|---|
| Product | `aliexpress.ds.product.get` | reachable |
| Category | `aliexpress.ds.category.get` | reachable |
| Search | `aliexpress.ds.text.search` | reachable |
| Order | `aliexpress.ds.trade.order.get` | reachable (`MissingParameter: order_id`) |
| Freight | `aliexpress.ds.freight.query` | reachable (`MissingParameter`) |
| Affiliate | `aliexpress.affiliate.product.query` | **correctly denied** (`InsufficientPermission`) |

The affiliate denial is retained deliberately as a control. It is what proves
the other five readings mean something: a real denial on this gateway has a
known shape, and no dropship API produced it. **No attempt was made to obtain
affiliate permission** — it is a different programme and Phase 4 does not use it.

**A parameter error counts as reachable** because the gateway validates
permission before parameters. This is inference from error codes, not a
successful business call.

## 5. Webhook verification

`POST /api/v1/integrations/aliexpress/webhook`

| Check | Result |
|---|---|
| 200 for a JSON object | pass |
| 200 for an empty body | pass |
| 200 for malformed JSON | pass |
| 200 for form-encoded | pass |
| 200 for an unknown content type | pass |
| 200 for a 300-field payload | pass |
| `GET` rejected with 405 | pass |
| Reachable through `https://api.whiteto.com` | pass |
| Payload **values** absent from logs | pass |
| OAuth callback unchanged | pass |

The tunnel delivery arrived carrying Cloudflare headers (`cf-ray`,
`cf-connecting-ip`), confirming it traversed the public path rather than
resolving locally.

**No real AliExpress webhook has ever been received.** Every delivery tested was
synthetic. AliExpress provides no test-callback trigger that was reachable from
here, so retry behaviour and the real payload shape remain unobserved.

Three defects were found and fixed during this release's audit:

1. **The payload was logged verbatim at INFO.** An order notification carries
   buyer names and addresses; `CLAUDE.md` forbids logging request bodies with
   customer data. Now only field *names*, a field count and header *keys* are
   logged; the body goes to DEBUG and only when `LOG_INCLUDE_REQUEST_BODY` is
   enabled, which `Settings` refuses in deployed environments. A unit test
   asserts the values never appear.
2. **No exception guard.** The module promised AliExpress would not retry, but
   an unreadable body (a client disconnecting mid-upload) would have produced a
   500 and provoked exactly that. Now guarded, with a test.
3. **A dead duplicate schema** (`AliExpressWebhookAck`) left by an abandoned
   edit. Removed.

## 6. Security review

| Control | Result |
|---|---|
| Secrets never logged | verified by searching the log for each decrypted value |
| Tokens encrypted at rest | Fernet envelope, decrypt round trip, plaintext absent |
| `SecretStr` for in-memory secrets | in use; masked in `repr` |
| OAuth state — CSRF | 256-bit, server-side, single-use, TTL-bounded |
| Replay prevention | verified live: second use of a state fails |
| Callback protection | tenant bound from state, not from a client value |
| Tenant isolation | enforced in `TenantScopedRepository._base_query()` |
| Permission boundaries | connect/disconnect admin-or-owner; status any role |
| Redis expiration | state TTL 600s, confirmed live |
| SQL injection | no user input interpolated; sort/filter allowlisted per model |
| JWT validation | `typ` claim checked; ≥32-char key enforced everywhere |
| CORS | explicit origin list; wildcard refused when deployed |
| Security headers | HSTS, `TrustedHostMiddleware`, wildcard host refused when deployed |

Issues found were fixed (webhook logging, exception guard) or recorded as debt
(M11 unsigned webhook, M12 unthrottled webhook). Nothing was left silently.

**XSS** is not separately verified. React escapes by default and no
`dangerouslySetInnerHTML` exists in the codebase, but no XSS-specific test
exists — this is an argument from construction, not a demonstration.

## 7. Quality gate results

| Gate | Result |
|---|---|
| `ruff check` | pass |
| `ruff format --check` | pass, 95 files |
| `mypy app` (strict) | pass, 78 source files |
| `pytest` (backend, all) | **291 passed** |
| `eslint` | pass (exit 0) |
| `tsc --noEmit` | pass (exit 0) |
| `next build` | pass, 13 routes |
| Playwright | **114 passed, 2 flaky, 0 failed** (exit 0, `--retries=2`) |

Backend: 231 unit + 60 integration. Integration tests build the schema by
running the migrations.

**On the Playwright number, honestly:** the first full run failed 14 tests. Those
were not defects — a `next start` server from hours earlier was being reused, so
the suite tested a stale bundle. After killing it, 5 failed; all 5 passed when
re-run serially; the suite is green with retries. The residual flakiness is real
and is recorded as M13. "114 passed" is accurate only with retries enabled.

## 8. Remaining limitations

1. **No business API call has been made through `AliExpressClient`.** All live
   probing used the application's signing helpers over raw HTTP. Request
   building, retry, error mapping and response deserialisation are still
   mock-only. (M10)
2. **No real webhook has been received.** All deliveries were synthetic.
3. **Webhook signatures are not verified**, and the endpoint is unthrottled.
   (M11, M12)
4. **Token refresh has never run against the live gateway.** Tokens expire
   2026-08-30.
5. **Docker has never been built or run.** (C1)
6. **The callback and webhook reach the backend through a development tunnel**
   forwarding to one machine. This is not a deployment.
7. **Local Redis is 3.0.504, pinned to RESP2**; production targets Redis 7.
   (M14)
8. **The AliExpress application is registered as 【AutoPilot】**, which is the
   name every seller sees on the consent screen.
9. **The dashboard renders mock data.** (M9)
10. **Affiliate APIs are denied.** Not required by Phase 4; not requested.

## 9. Known risks

| Risk | Likelihood | Impact |
|---|---|---|
| Response schemas do not match real payloads | **High** | Phase 4 product import fails until corrected |
| First Docker build fails | Medium | Blocks any deployment; unknown time to fix |
| Webhook payload shape differs from assumptions | Medium | Parser rewrite; contained to one module |
| Token refresh defect surfaces at expiry | Medium | Connections silently break after 2026-08-30 |
| Flaky E2E masks a real regression | Medium | A defect ships believed to be flake |

The first is the one to plan around. Everything that carries a credential has
now been verified against the live gateway; nothing about the *shape of business
data* has.

## 10. Production readiness

**Not production ready. Not beta ready. Ready for continued development, and
the integration foundation is sound.**

| Question | Answer | Why |
|---|---|---|
| Production? | **No** | The deployment path has never been executed once (C1). Docker, Compose and Nginx are unbuilt. Nothing can be deployed, so nothing can be production ready. |
| Beta? | **No** | Beta implies real sellers with real orders. The webhook is unsigned and unthrottled, no business API call has been proven, and the dashboard shows mock data. |
| Development? | **Yes** | Architecture, multi-tenancy, authentication, encryption and the OAuth integration are verified working against real infrastructure. |

**Score: 62/100.**

What earns it: clean layering held under three phases of pressure; multi-tenancy
enforced in one place; credentials encrypted with a working rotation path; OAuth
verified end to end against a live gateway including replay protection; 291
backend tests and 116 E2E tests; documentation that records decisions and
corrects itself.

What costs it: the deployment path has never run (−20); no business API call or
real webhook has ever been exercised (−10); the webhook is unsigned and
unthrottled (−5); E2E flakiness (−3).

The gap is verification and deployment, not design.

## 11. Recommendation

**Proceed to Phase 4**, with product import first and the response schemas
treated as unverified until a real payload parses. That is where M10 will
surface, and it is better surfaced early in a phase than late.

Two things are worth doing in parallel, neither blocking: resolve C1 by building
the Docker stack (it needs administrator access), and capture one real webhook
delivery to settle whether AliExpress signs them.
