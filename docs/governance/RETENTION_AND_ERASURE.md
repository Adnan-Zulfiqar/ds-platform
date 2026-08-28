# Retention and erasure

Two halves: what the code enforces today, and the runbook for a request that
arrives by email.

The distinction matters more than it sounds. A retention period in a privacy
notice with nothing enforcing it is a promise nobody is keeping, and the first
person to check will find that out.

---

## What is actually enforced

**Enforced** means a timer, a TTL or a delete statement makes it true without
anyone remembering to act.

| Data | Period | Enforced by | Evidence |
|---|---|---|---|
| Access token | 15 minutes | JWT expiry; held in browser memory only, lost on reload | `security.access_token_ttl_minutes` |
| Refresh token | 30 days | Row expiry, rotation on every use, revocation on sign-out | `security.refresh_token_ttl_days`, `app/models/refresh_token.py` |
| Email verification token | 24 hours | Row expiry | `security.email_verification_ttl_hours` |
| eBay OAuth state | 600 s | Redis TTL | `ebay.oauth_state_ttl_seconds` |
| Shopify OAuth state | 600 s | Redis TTL | `shopify.oauth_state_ttl_seconds` |
| AliExpress OAuth state | 600 s | Redis TTL | `aliexpress.oauth_state_ttl_seconds` |
| Rate-limit counters | 60 s window | Redis TTL | `security.rate_limit_window_seconds` |
| Login attempt counters | 300 s window | Redis TTL | `security.login_attempt_window_seconds` |
| Login lockout | 900 s | Redis TTL | `security.login_lockout_seconds` |
| Cache entries | 300 s default | Redis TTL | `redis.default_ttl_seconds` |
| Marketplace credentials | Until disconnect | **Hard delete** of the row and its ciphertext | see below |
| eBay connection on account deletion | On notification | Hard delete across every workspace | `app/integrations/ebay/deletion.py` |
| Rotated log files | 30 days | `scripts/prune_logs.py`, **only where logs are persisted** | [LOG_RETENTION.md](LOG_RETENTION.md) |

### Not enforced by a timer

| Data | Reality | Why |
|---|---|---|
| Account and workspace data | Kept while the account exists | Deleted on request via the runbook below |
| Products, drafts, stores | Kept while the workspace exists | The merchant's business records |
| Order and buyer data | Kept while the workspace exists | Held as processor; the merchant decides |
| eBay compliance ledger | **Indefinite, deliberately** | It is the evidence of compliance. Contains no personal data |
| Application logs | **Not persisted at all today** | Logger writes to stdout; production redirects nothing to disk |
| Privacy mailbox | No schedule | Open operator decision |

### Marketplace credential destruction — verified

All three providers hard-delete. This was checked in the code, not assumed:

| Provider | Path | Result |
|---|---|---|
| eBay | `EbayConnectionService.disconnect` → `connections.hard_delete(connection)` | Row and ciphertext gone |
| AliExpress | `AliExpressService.disconnect` → `connections.hard_delete(connection)` | Row and ciphertext gone |
| Shopify | `ShopifyService.disconnect` → `release_shop` → `session.delete(connection)` | Row and ciphertext gone, plus best-effort webhook removal and remote revocation |

No provider token is exposed in any API response: `EbayConnectionRead` has no
field capable of carrying one, and the same holds for the Shopify and AliExpress
read schemas. Tokens are redacted from logs by
`_SENSITIVE_FIELD_MARKERS` in `app/core/logging.py`, which matches `token`,
`secret`, `password` and `authorization` as substrings. Nothing writes a token
to browser storage.

**eBay revocation is local only, and deliberately so.** eBay documents that
sellers revoke consent through their own account pages and publishes no
application-callable revocation endpoint for this grant. The notice says so
rather than implying we revoke it upstream.

---

## Data-subject request runbook

Owner: **DESIRLY LIMITED privacy operations**, via `privacy@whiteto.com`.
Operational contact: Adnan Zulfiqar.

**Targets.** Acknowledge promptly; complete within **one month** of receipt, as
UK GDPR requires. Record the received and completed dates for every request.

### 1. Verify who is asking

Do not act on an unverified request. An erasure carried out for an impersonator
is a data breach with extra steps. Confirm the request comes from the address on
the account, or verify by another route already associated with it. Record how
verification was done.

### 2. Rehearse

```bash
cd backend
python scripts/erase_data_subject.py --email person@example.com
```

Reads and counts only. Prints the declared categories and the row counts each
would touch. **Nothing is written.** If the address does not resolve, the script
says so and exits 1 — check for a typo before concluding there is no account.

### 3. Review the plan

Confirm the counts look like one person's account and not, say, a thousand
orders belonging to a workspace you did not expect. If anything is surprising,
stop and investigate rather than proceeding.

### 4. Execute

```bash
python scripts/erase_data_subject.py --email person@example.com --apply
```

Requires typing the subject's address to confirm. What happens:

| Category | Action |
|---|---|
| Refresh tokens | Deleted |
| Email verification tokens | Deleted |
| Role assignments | Deleted (physically — a soft-deleted grant still grants) |
| Notifications | Deleted |
| Shopify / AliExpress / eBay connections | Deleted, including encrypted credentials |
| Stores | `connected_by_user_id` cleared |
| Orders | Buyer and recipient fields cleared; commercial figures kept |
| User row | Email replaced with `erased-<id>@erased.invalid`, names and password hash cleared, account deactivated |

The user row survives because orders, products and audit rows reference it. A
cascade would destroy a merchant's business records to satisfy a request about
one person. Erasure is achieved by removing what identifies them.

**Idempotent** — a repeat run reports zeroes rather than failing, so an
interrupted run can simply be re-run.

### 5. Record and respond

Record the subject id, the dates, and the counts the script printed. **Do not
paste the person's address into the application log** — the script deliberately
never does, and a request record that reintroduces the address defeats the
erasure.

Tell the person what was erased and what was kept, and why. The honest list is
the table above.

### What this runbook cannot do

* **It does not reach backups**, because there are none. See
  [BACKUPS.md](BACKUPS.md). When backups exist this section must be rewritten
  before it is relied on.
* **It does not erase a shopper.** A buyer whose details arrived through a
  merchant's order sync should be directed to that merchant, who is the
  controller. DESIRLY LIMITED acts on the merchant's instructions.
* **It does not touch the eBay compliance ledger**, which holds no personal
  data.
* **It does not cover the privacy mailbox**, which is manual.

### Tests

`backend/tests/integration/test_data_subject_erasure.py` covers resolution,
dry-run purity, anonymisation, idempotency, the declaration's completeness, that
the subject string carries no personal data, and — the one that matters most —
that erasing one tenant leaves another untouched.
