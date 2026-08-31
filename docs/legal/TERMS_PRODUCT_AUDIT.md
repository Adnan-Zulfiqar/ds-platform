# Terms of Service — product audit and clause mapping

Audited at `5aaa6ec709e785cbb87989e2c1d324ef8d5a36e3` by reading the code, not
the roadmap. Every commercial statement in `/terms` traces to a row below.

The point of this document is the **negative** column. It is easy to write a
Terms of Service that describes a product; it is easy, and expensive, to
describe one that does not exist yet.

---

## 1. What the application actually does

| Area | Verified behaviour | Where |
|---|---|---|
| Account creation | `POST /auth/register` creates tenant, first user and owner role in one transaction, then signs the user in | `app/services/auth.py` |
| Tenant lifecycle | New tenants start at `TenantStatus.TRIAL` | `app/services/auth.py:241` |
| Roles | Owner assigned at registration; authorisation enforced as a FastAPI dependency | `app/models/role.py`, `app/api/deps.py` |
| Authentication | Password (Argon2id) and Google Identity Services. Sign-in, sign-up, link, unlink | `app/api/v1/auth/router.py` |
| Step-up | Linking or unlinking Google requires the account password, rate limited per user and per address | `app/services/login_throttle.py` |
| Password reset | Six-digit code by email, HMAC at rest, ten-minute expiry, five attempts | `app/services/password_reset.py` |
| Email verification | Implemented | `app/services/email_verification.py` |
| Marketplace connections | AliExpress, Shopify, eBay | `app/integrations/` |
| eBay | **Connection only.** No listing or order import yet | `docs/governance/SUBPROCESSORS.md` |
| Imports and drafts | Product import, drafts, product editor | `app/services/product_import.py`, `app/api/v1/drafts/` |
| Pricing and rules | Pricing engine, global pricing and shipping rules, rule application queue | `app/services/pricing_engine.py`, `global_rules.py` |
| Orders | Order sync and order endpoints | `app/services/order_sync.py` |
| Inventory | Inventory sync | `app/services/inventory_sync.py` |
| Automation | Automation service plus Celery tasks | `app/services/automation_service.py`, `app/tasks/` |
| Analytics | In-product analytics only. **No third-party analytics or tracking** | `app/services/analytics_service.py` |
| AI output | `AI_PROVIDER` defaults to `stub`, which generates locally and sends nothing externally | `docs/governance/SUBPROCESSORS.md` |
| Erasure | Operator-run CLI, with a ledger | `backend/scripts/erase_data_subject.py` |
| Cache invalidation on erasure | `CacheClient.invalidate_tenant` using `SCAN`, never `KEYS` | `app/core/redis.py` |

## 2. What it does **not** do

Each of these is a sentence the Terms could easily have contained and does not.

| Absent | Evidence | How `/terms` handles it |
|---|---|---|
| **Billing of any kind** | No `billing`, `subscription`, `plan`, `invoice` or `payment` model. No payment SDK in `pyproject.toml` or `package.json`. `app/models/tenant.py` says billing state is "deliberately absent" | §11 describes Plans conditionally and states plainly that **no paid Plan is on sale**; §12 describes failed payment without implying a payment system exists |
| **Payment processor** | None configured | Not mentioned as existing |
| **Backups** | `docs/governance/BACKUPS.md`: "**There are none.** Not 'untested', not 'informal' — none." | §21 says so in the Terms, in as many words |
| **Data export feature** | No export endpoint, service or task | §23 says there is no self-service export and gives the manual route |
| **Self-service cancellation** | No endpoint. `TenantStatus.CANCELLED` exists as an enum value only | §13 says cancellation is by email |
| **Suspension enforcement** | `TenantStatus.SUSPENDED` exists, but **`app/api/deps.py` contains zero `TenantStatus` checks**. The status only filters which tenants background tasks process | §22 is drafted as a **reserved right**, not a described capability. It does not claim access is automatically denied |
| **Uptime / SLA** | No monitoring, no availability target, no status page | §21 states there is no SLA and no uptime commitment |
| **Support SLA** | Email only, no ticketing | §34 says no guaranteed response time is published |
| **Free trial** | No trial logic. `TenantStatus.TRIAL` is the default status, not a time-limited commercial trial | §15 says no free trial is offered |
| **TikTok integration** | None anywhere in the repository | §8 names TikTok only in the no-affiliation disclaimer |
| **Executed DPA** | `SUBPROCESSORS.md`: no DPA signed with Resend or Google | §10 states no DPA has been signed and gives the contact |
| **ICO registration** | `docs/governance/ICO_REGISTRATION_GATE.md` — not registered | Not claimed anywhere |
| **Security certification** | None | Not claimed. Tests assert ISO 27001 / SOC 2 / PCI-DSS do **not** appear |

## 3. Clause-to-behaviour map

| Terms clause | Rests on | Status |
|---|---|---|
| §1 Company identity | Companies House record 16381500 | Verified against the register |
| §3 B2B only, 18+, authority | Commercial position; no age or business check is implemented | **Contractual assertion, not a technical control.** Recorded for the solicitor |
| §4 Contract formation | `LegalAcceptance.require_valid`; versions stored on `users` (migration `0032`) | Implemented and tested |
| §4 Version authority | Server compares the submitted version to its own constant and refuses a mismatch | Implemented and tested |
| §5 Credentials, step-up | Argon2id, Google link/unlink step-up with rate limiting | Implemented |
| §6–§7 Service and integrations | The features listed in §1 above | Implemented |
| §8 No affiliation | No partnership exists with any platform | True |
| §9 Merchant responsibilities | Allocation of risk; no technical control | Contractual |
| §10 Controller/processor roles | Mirrors `docs/governance/PROCESSING_REGISTER.md` | Consistent |
| §11–§15 Plans, cancellation, refunds, trials | **No billing implementation.** Drafted to bind only when a Plan is purchased | Conditional |
| §16 Acceptable use | Partly enforced: rate limits, tenant isolation in `TenantScopedRepository`. The rest is contractual | Mixed, stated honestly |
| §17 Content licence | Scoped to operating the Service, including publishing to a Connected Service on instruction | Matches what the code does |
| §21 Availability, backups | `BACKUPS.md` | Honest negative |
| §22 Suspension | Reserved right; enforcement not implemented | **Right reserved, capability absent** |
| §23 Closure, export, deletion | Operator-run erasure script; retention per `RETENTION_AND_ERASURE.md` | Consistent |
| §24 No guaranteed results | AI output is a suggestion; marketplaces decide their own enforcement | True |
| §26 Liability | UCTA 1977 s.2, s.3, s.11; Misrepresentation Act 1967 s.3 | See `TERMS_LEGAL_REVIEW.md` |
| §32 Third-party rights | Contracts (Rights of Third Parties) Act 1999 s.1 | Standard exclusion |

## 4. Things that would make a clause untrue if changed

* Implementing suspension enforcement would make §22 describe a capability
  rather than a right — an improvement, and the clause would not need editing.
* Adding billing makes §11 operative. The "no paid Plan is on sale" sentence
  must be removed **in the same change**, or it becomes a false statement in a
  contract.
* Adding backups requires §21 to change, and the privacy notice with it.
* Adding a data export feature requires §23 to change.
* Enabling a real AI provider moves listing text to a third party and changes
  both §24 and the privacy notice's sharing section.
