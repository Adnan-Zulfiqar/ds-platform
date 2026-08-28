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

## Data-subject requests

The runbook moved to its own document: **[DATA_SUBJECT_REQUESTS.md](DATA_SUBJECT_REQUESTS.md)**.
It covers identity verification, the three subject scopes, the database identity
check, rollback, the one-month deadline and what to record.

The essentials, so this document is not misleading on its own:

| Scope | Identified by | Effect |
|---|---|---|
| `platform-user` | tenant **and** user | That person's credentials, grants and notifications deleted; all seventeen columns referencing them deleted or cleared; account anonymised. **Colleagues, orders and marketplace credentials untouched.** |
| `workspace` | tenant | Every member erased, plus the workspace's Shopify, AliExpress and **eBay** connections deleted with their encrypted credentials |
| marketplace buyer | — | **Not implemented.** Referred to the merchant; a launch blocker |

Dry run is the default, `--expect-database` is mandatory and verified against
`SELECT current_database()`, confirmation is typed, everything runs in one
transaction, and the operation is idempotent.

### What it cannot do

* **It does not reach backups**, because there are none ([BACKUPS.md](BACKUPS.md)).
* **It does not erase a shopper.** `orders` holds no buyer identifier, so exact
  matching is impossible; approximating it destroyed uninvolved customers'
  records in an earlier version and is now forbidden and tested against.
* **It does not touch the eBay compliance ledger**, which holds no personal data.
* **It does not cover the privacy mailbox**, which is manual.

### Tests

`backend/tests/integration/test_data_subject_erasure.py` — 26 tests covering
declaration completeness against the live schema, per-tenant resolution, foreign
tenant isolation, single-user erasure in a multi-member workspace, survival of
shared connections and order data, workspace closure including eBay, the
database guard, transaction rollback after an injected failure, idempotency,
Redis key exactness, and that no address reaches a log line.
