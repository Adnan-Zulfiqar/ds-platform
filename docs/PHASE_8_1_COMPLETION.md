# Phase 8.1 — completion report

**Shopify OAuth to production quality (App URL install + hardening).**

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` (**not** `main`) |
| Tag | `phase-8-1-complete` on `develop` |
| Plan | [PHASE_8_1_PLAN.md](PHASE_8_1_PLAN.md) |
| Depends on | Phase 8 (`phase-8-complete`), install-flow audit |

---

## 1. What shipped

| Area | Outcome |
|---|---|
| App URL install | `GET /api/v1/integrations/shopify/install` — HMAC verify → authorize or claim ticket |
| Tenant claim | `POST /shopify/claim-install` binds verified install to current admin tenant |
| Typed Connect | Still domain-only (`POST /connect`); dialog UX; never asks for keys/tokens |
| Domain rules | Reject localhost / custom domains; normalise scheme/path/case/bare handle |
| Webhooks | Base must end `/webhooks`, `/callback`, or `/webhook`; singular route added; idempotent register |
| Disconnect | Best-effort remote webhook delete + token revoke, then local hard-delete |
| Uninstall | Releases global `shop_domain` claim (deletes connection) |
| Reconnect | Reuses existing store by slug (fixes `uq_stores_tenant_slug`) |
| Mutating webhooks | Processing failures raise so Shopify retries (no silent ACK) |
| Frontend | Dialog connect, claim_needed auto-continue, reconnect, clearer status, no env-secret copy |
| Config | `.env.example` documents App URL + webhook base rules |

Merchants are **never** asked for API key, secret, or access token.

---

## 2. Commits

| Hash | Message |
|---|---|
| `b4b2bcf` | docs(shopify): audit install flow and write Phase 8.1 plan |
| `59ae05e` | feat(shopify): App URL install, claim, and OAuth hardening |
| `b4fa464` | feat(frontend): Shopify connect dialog and claim-install UX |
| `1192e4b` | test(shopify): cover App URL install and dialog OAuth flow |
| `598306b` | docs: close Phase 8.1 with honest live-verification gaps |
| `ccb70b6` | docs: record Phase 8.1 tip hash and commit list |
| `f8adb79`+ | tip-hash pin commits (see `git log phase-8-1-complete`) |

Use `git rev-parse phase-8-1-complete^{commit}` for the exact tip.

> **Tag honesty:** `phase-8-1-complete` was created and pushed **before** frontend
> production build, Playwright re-run, and live OAuth were fully verified. That
> tag was **not moved**. Follow-up verification is recorded in
> [PHASE_8_1_VERIFICATION.md](PHASE_8_1_VERIFICATION.md) without rewriting the
> original tag tip.

---

## 3. Quality gates

### At tag time (`phase-8-1-complete`)

| Gate | Result |
|---|---|
| `ruff check .` | Pass |
| `ruff format --check .` | Pass |
| `mypy app` (strict) | Pass (158 files) |
| `pytest` | **767 passed** |
| `npm run lint` | Pass |
| `npm run typecheck` | Pass |
| `npm run build` | **Not verified** at tag time (hung behind locked `.next/standalone`) |
| Playwright Shopify OAuth | Spec updated; **not re-run** at tag time |

### Follow-up verification (2026-08-03, after tag — see VERIFICATION doc)

| Gate | Result |
|---|---|
| `npm run build` | **Pass (exit 0)** after stopping e2e lock + clearing `.next` |
| Playwright Shopify (chromium + mobile) | **26 passed** |
| Playwright integrations + shell | **62 passed** |
| Live Shopify OAuth round trip | **Still not verified** (M17) |

---

## 4. Live verification (honest)

| Step | Status |
|---|---|
| Install URL HMAC path (unit) | Verified with synthetic HMAC |
| Production `GET …/install` | **404** — Phase 8.1 not deployed to `api.whiteto.com` |
| Consent page (browser) | **Not verified** — needs human session; unauthenticated curl ends **403** on `admin.shopify.com` (expected without login). Prior browser “Unauthorized Access” still open (M17). |
| Callback / token exchange / DB row | **Not verified** live |
| Webhook registration against Shopify | **Not verified** live |
| Disconnect / reconnect live | **Not verified** live |

**Gap (M17):** Partner Dashboard settings cannot be read from the repo. Deploy `/install` before pointing App URL at it. Complete one browser consent on `mriy3s-zv.myshopify.com`.

**Webhook base:** Local `.env` uses singular `…/webhook`. Production probes: only POST `/callback` is alive (**401** without HMAC); `/webhook` and `/webhooks/*` return **404**. Until the tunnel exposes those paths, point `SHOPIFY_WEBHOOK_CALLBACK_BASE` at the callback URL.

---

## 5. Production readiness score (Shopify OAuth slice)

| Area | Score | Notes |
|---|---|---|
| OAuth security (HMAC, state, encryption) | 8 | App URL + claim paths; live consent unproven |
| Merchant UX (no secrets) | 9 | Domain-only dialog; App URL claim path |
| Webhooks | 7 | Code OK; production `/webhook(s)` still 404 |
| Tenant isolation / shop claim | 8 | Global uniqueness preserved; uninstall releases claim |
| Automated gates (build + Playwright) | 9 | Verified after tag — see VERIFICATION doc |
| Live Partner install | 2 | M17; production `/install` 404 until deploy |
| **Overall Phase 8.1** | **~6.5 / 10** | Implementation-complete; **not** live-verification-complete |

---

## 6. Limitations

1. Live Shopify OAuth consent not completed (M17).
2. Production API has not deployed Phase 8.1 `/install` (404).
3. Disconnect remote revoke is best-effort; local cleanup always proceeds.
4. Embedded App Bridge / session tokens still out of scope.
5. Playwright disconnect/reconnect against a real connected store still needs live Partner credentials.
6. Full Playwright suite (all specs) was not re-run in the verification pass — Shopify + integrations + shell were.

---

## 7. Docs touched

- `docs/PHASE_8_1_PLAN.md` (audit + plan)
- `docs/PHASE_8_1_COMPLETION.md` (this file)
- `docs/PHASE_8_1_VERIFICATION.md` (follow-up gates; tag not moved)
- `PROJECT_ROADMAP.md`, `CHANGELOG.md`, `TECHNICAL_DEBT.md`
- Prior: `SHOPIFY_INSTALL_FLOW_AUDIT.md`, `SHOPIFY_PRODUCTION_INSTALL_PLAN.md`
