# Phase 8.1 — completion report

**Shopify OAuth to production quality (App URL install + hardening).**

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` (**not** `main`) |
| Tag | `phase-8-1-complete` (pending push) |
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

## 2. Commits (logical stages)

Documented after git push — see `git log` on `develop` for Phase 8.1 range.

Expected sequence:

1. Docs: prior install audit + `PHASE_8_1_PLAN.md`
2. Backend: install/claim, webhooks, disconnect/uninstall, normalisation
3. Frontend: Connect dialog + claim + status UX
4. Tests + Playwright updates
5. Completion docs / roadmap / changelog / debt

---

## 3. Quality gates (verified this session)

| Gate | Result |
|---|---|
| `ruff check .` | Pass |
| `ruff format --check .` | Pass |
| `mypy app` (strict) | Pass (158 files) |
| `pytest` | **767 passed** |
| `npm run lint` | Pass |
| `npm run typecheck` | Pass |
| `npm run build` | **Not verified** — concurrent `next build` processes hung after the Next.js banner in this environment; lint/typecheck passed. Re-run build on a clean machine before release. |
| Playwright Shopify OAuth | Updated for dialog flow; **not re-run** in this session (needs live API + Redis). |

---

## 4. Live verification (honest)

| Step | Status |
|---|---|
| Install URL HMAC path (unit) | Verified with synthetic HMAC |
| Consent page (browser) | **Not verified** — Partner authorize still **403** for `mriy3s-zv.myshopify.com` (M17) |
| Callback / token exchange / DB row | **Not verified** live |
| Webhook registration against Shopify | **Not verified** live |
| Disconnect / reconnect live | **Not verified** live |

**Gap (unchanged M17):** Partner Dashboard App URL, Allowed redirection URL, and distribution/install eligibility must be confirmed by a human. Code now exposes App URL `…/shopify/install`; until Dashboard points there and authorize returns 200/consent, live OAuth remains incomplete.

**Webhook base:** Local `.env` historically used singular `…/webhook`. Code now accepts it via `POST …/shopify/webhook`. Prefer `…/webhooks` or shared `…/callback` in production.

---

## 5. Production readiness score (Shopify OAuth slice)

| Area | Score | Notes |
|---|---|---|
| OAuth security (HMAC, state, encryption) | 8 | App URL + claim paths; live consent unproven |
| Merchant UX (no secrets) | 9 | Domain-only dialog; App URL claim path |
| Webhooks | 8 | Validated bases, idempotent register, fail-closed mutate |
| Tenant isolation / shop claim | 8 | Global uniqueness preserved; uninstall releases claim |
| Live Partner install | 3 | M17 still open |
| **Overall Phase 8.1** | **~7 / 10** | Code ready; Dashboard + live OAuth still required |

---

## 6. Limitations

1. Live Shopify OAuth consent not completed (M17).
2. Frontend `next build` hung in this session — re-verify before deploy.
3. Disconnect remote revoke is best-effort; local cleanup always proceeds.
4. Embedded App Bridge / session tokens still out of scope.
5. Playwright disconnect/reconnect against a real connected store still needs live Partner credentials.

---

## 7. Docs touched

- `docs/PHASE_8_1_PLAN.md` (audit + plan)
- `docs/PHASE_8_1_COMPLETION.md` (this file)
- `PROJECT_ROADMAP.md`, `CHANGELOG.md`, `TECHNICAL_DEBT.md`
- Prior: `SHOPIFY_INSTALL_FLOW_AUDIT.md`, `SHOPIFY_PRODUCTION_INSTALL_PLAN.md`
