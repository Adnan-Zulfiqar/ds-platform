# AliExpress integration

How a tenant connects their AliExpress account, how their credentials are
protected, and why each decision was made that way.

> ## ⚠️ Verify the API contract before live traffic
>
> The endpoints, signing scheme, and error codes here implement the documented
> AliExpress Open Platform behaviour, **but none of it has been exercised
> against the real API.** AliExpress operates several regional gateways and has
> changed both signing methods and paths between API generations.
>
> Before connecting a real account, confirm against current AliExpress developer
> documentation for the account in use:
>
> * the authorize, token, refresh, and gateway URLs
> * the signing algorithm and which parameters are excluded
> * the error codes mapped in `client.py`
> * the token response field names
>
> Every one of these is either a setting or isolated in a single function,
> specifically so that correcting one is a configuration change or a one-function
> edit rather than a rewrite. See [Correcting the contract](#correcting-the-contract).

## Contents

1. [Architecture](#architecture)
2. [OAuth flow](#oauth-flow)
3. [Credential handling](#credential-handling)
4. [Security model](#security-model)
5. [API client](#api-client)
6. [Rate limiting](#rate-limiting)
7. [Background tasks](#background-tasks)
8. [Configuration](#configuration)
9. [Correcting the contract](#correcting-the-contract)
10. [Known limitations](#known-limitations)

---

## Architecture

```
app/integrations/aliexpress/
├── exceptions.py   typed failures, each declaring whether it is retryable
├── schemas.py      wire models (theirs) and API models (ours)
├── auth.py         request signing + OAuth state
├── client.py       the only code that talks to AliExpress over the network
└── service.py      connection lifecycle; knows nothing about HTTP
```

Layering matches the rest of the platform:

```
router → service → client → AliExpress
              ↓
         repository → PostgreSQL
```

**No AliExpress logic appears in a router.** Endpoints depend on the service,
which is why the same flows are driven by a Celery task without duplication —
the health check calls `AliExpressService.check_health` directly.

The client is the only place `httpx` appears. Callers never see a status code or
an AliExpress error string; they see a typed exception. That boundary is what
makes the rest of the application testable without a network.

---

## OAuth flow

```
1. POST /api/v1/integrations/aliexpress/connect     (admin or owner)
      ├─ app secret encrypted and stored
      ├─ connection created as `pending`
      ├─ random `state` stored in Redis with a TTL
      └─ returns authorization URL

2. Browser → AliExpress consent screen

3. GET /api/v1/integrations/aliexpress/callback?code=…&state=…
      ├─ state looked up and DELETED (single use)
      ├─ tenant in state compared with the caller's tenant
      ├─ code exchanged for tokens, signed with the stored secret
      ├─ tokens encrypted, status → `connected`
      └─ 303 redirect back into the application
```

### Why `connect` is a POST, not the GET named in the phase brief

It has side effects — it writes encrypted credentials and issues a single-use
token — and it accepts a secret in its body. A secret in a URL lands in browser
history, proxy logs, and the `Referer` header of every subsequent request. The
response carries the authorization URL for the client to navigate to, which
achieves the same outcome without putting the credential in a URL.

### Why the `state` parameter is not optional

**This is the CSRF defence for the OAuth redirect.** Without it, an attacker can
complete a consent flow with *their own* AliExpress account and deliver the
resulting code to a victim's browser. The victim's workspace would then be bound
to the attacker's supplier account, and every order the victim fulfils would be
placed through it — with the attacker choosing the products, the prices, and
where the money goes.

Three properties make it work:

* **Random.** 256 bits from `secrets`, never `random`.
* **Server-side.** The binding (which tenant and user began the flow) lives in
  Redis. Putting the tenant id inside the token would let a caller edit it.
* **Single use.** Deleted on lookup, so a replayed callback fails.

**It fails closed.** If Redis is unavailable the flow refuses to start, because
a state that cannot be stored cannot be verified on return — and an unverifiable
callback is exactly what the defence exists to prevent.

### Why the callback redirects instead of returning JSON

The browser arrives directly from AliExpress's consent screen, so the user must
end up on a page. Failures redirect too, carrying a short reason
(`?aliexpress=denied|failed|invalid`) drawn from a fixed vocabulary this
application controls — never an upstream message, which could be reflected into
the page.

---

## Credential handling

| Value | Storage | Why |
|---|---|---|
| `app_key` | Plaintext | A public identifier; it travels in every request URL |
| `app_secret` | **Fernet ciphertext** | Signs every request; recoverable, so encrypted not hashed |
| `access_token` | **Fernet ciphertext** | Same |
| `refresh_token` | **Fernet ciphertext** | Same |

**Encryption, not hashing.** Passwords are hashed because they only need
verifying. These values must be *recovered* to sign outbound requests.

**Fernet** — AES-128-CBC with an HMAC-SHA256 tag and a random IV per message.
Authenticated, so tampering is detected rather than yielding wrong plaintext;
randomised, so encrypting the same token twice gives different ciphertext and an
observer cannot tell which tenants share a credential.

### Key rotation

Keys are configured **newest first** in `SECURITY_ENCRYPTION_KEYS`. Encryption
always uses the first; decryption tries each in turn.

```
1. Prepend a new key.          Old values still decrypt.
2. Re-encrypt stored rows.     `encryption.rotate()` per row.
3. Remove the old key.         Nothing references it any more.
```

There is no window in which existing data cannot be read, which is what makes
rotation possible without downtime.

### Nothing returns a credential

The response models in `schemas.py` **have no field capable of holding one** —
not the secret, not a token, not even a masked token. A credential cannot leak
through the API because there is nowhere to put it. `_to_read_model` in the
router copies fields explicitly rather than validating the ORM object wholesale,
so adding an encrypted column to the model can never cause it to appear in a
response.

Covered by tests that assert the absence directly, at both the API and browser
levels.

---

## Security model

| Concern | Mechanism |
|---|---|
| Credentials at rest | Fernet, key from environment, rotation supported |
| Credentials in transit | TLS to AliExpress; the secret is an HMAC key, never a transmitted parameter |
| Credential exposure via API | Structurally impossible — no response field exists |
| CSRF on the OAuth redirect | Single-use, server-side, random `state`; fails closed |
| Cross-tenant access | Tenant-scoped repository; the connection table is unique per tenant |
| Privilege | `connect` and `disconnect` require admin or owner; `status` is readable by any role |
| Credential retention | Disconnect **deletes the row** — see below |
| Log exposure | No credential, ciphertext, or response body is logged; only endpoint, outcome, and upstream error code |

### Why disconnect hard-deletes

Everything else in this platform soft-deletes. This does not.

A customer who disconnects has asked us to forget their credentials. Retaining
an encrypted copy is retention they did not request, and it enlarges the blast
radius of any future key compromise for no benefit. The *event* is recorded in
the application log; the secret is not kept.

---

## API client

Three behaviours worth knowing, each easy to get wrong and expensive to get
wrong.

**AliExpress reports failure inside HTTP 200.** The body is inspected on every
response regardless of status. Trusting the status code alone means treating a
rejected signature as success and storing a token that does not exist.

**Only transient failures are retried.** Every exception declares `retryable`.
A timeout or 5xx is retried with exponential backoff and **full jitter**; a
rejected app secret is not. Retrying a wrong secret three times wastes quota and
delays telling the user what they must fix.

The jitter matters: when a provider has an outage every pending request fails at
once, and without it they all retry at the same instant and arrive as a
synchronised wave — which is what stops the provider recovering.

**Credentials never reach a log.** Failures are logged by endpoint, outcome, and
upstream error code. Never the request parameters (which carry the app key and
signature) and never the response body (which carries tokens).

---

## Rate limiting

Two separate limiters, protecting opposite directions:

| | `middleware/rate_limit.py` | `integrations/rate_limiter.py` |
|---|---|---|
| Protects | Us from our callers | A provider from us |
| Keyed on | Tenant, falling back to IP | Tenant **and** provider |
| On Redis failure | **Fails open** | **Fails closed** |

The opposite failure modes are deliberate. Failing open inbound keeps our own
API available during a Redis outage. Failing closed outbound is right because
exceeding a provider's quota does not just fail one request — providers throttle
or suspend an application key after sustained abuse, which takes the integration
down for **every tenant**.

Counted per tenant so one busy workspace cannot consume the whole allowance and
starve the others.

---

## Background tasks

`app/tasks/integrations/aliexpress.py`

| Task | Purpose |
|---|---|
| `integrations.aliexpress.health_check` | Refresh a near-expiry token; record whether the connection is usable |
| `integrations.aliexpress.sweep_health_checks` | Find connections nearing expiry and fan out a check for each |

**Tenant context is bound per connection, not per task.** The sweep runs on no
tenant's behalf; each connection belongs to exactly one. Binding inside the loop
keeps every scoped query correct, and clearing it afterwards stops one tenant's
context leaking into the next iteration — worker threads are reused.

Tasks are idempotent, which they must be: `task_acks_late` gives at-least-once
delivery, so any task can legitimately run twice.

Integration work routes to its own `integrations` queue, because it is bounded
by a third party's latency rather than our capacity. A supplier having a slow
morning must not delay anything else.

**Nothing schedules the sweep yet.** Celery beat configuration belongs with the
phase that needs regular synchronisation.

---

## Configuration

```bash
# Required for any credential storage. Newest key first, comma-separated.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECURITY_ENCRYPTION_KEYS=<key>

# Must match the AliExpress developer console exactly, including scheme and
# any trailing slash. A mismatch is rejected before a code is issued, and the
# error appears on AliExpress's page rather than in our logs.
ALIEXPRESS_REDIRECT_URI=http://localhost:8000/api/v1/integrations/aliexpress/callback
ALIEXPRESS_FRONTEND_RETURN_URL=http://localhost:3000/settings/integrations
```

Full list in `.env.example` under the AliExpress section.

**Without `SECURITY_ENCRYPTION_KEYS` the application starts normally but
refuses to store credentials.** That is deliberate — refusing beats writing a
customer's supplier secret in plaintext because a key was missing — but it means
the failure appears at first use rather than at boot.

---

## Correcting the contract

If AliExpress's API differs from what is implemented here, the change is
localised by design:

| What is wrong | Where to fix it |
|---|---|
| An endpoint URL | `ALIEXPRESS_*_URL` — configuration, no code change |
| The signing algorithm | `auth.sign_request` — one function |
| Excluded signature parameters | `auth._UNSIGNED_PARAMS` |
| Error code meanings | The three frozensets at the top of `client.py` |
| Token response fields | `schemas.AliExpressTokenResponse` (already `extra="allow"`) |
| Where the token sits in the payload | `service._parse_token` |

---

## Known limitations

1. **Never tested against the real AliExpress API.** See the banner above. The
   signing scheme, endpoints, and error codes are implemented from documentation
   and verified only against mocks.

2. **One connection per tenant.** Enforced by a unique constraint. Multiple
   supplier accounts are a real future requirement, but supporting them now
   would mean guessing how orders route between them. Relaxing this later drops
   a constraint; tightening it later would mean reconciling duplicate rows.

3. **No product, price, inventory, or order functionality.** Phase 3 is the
   connection foundation only.

4. **The sweep is not scheduled.** `sweep_health_checks` exists and works;
   nothing runs it periodically yet.

5. **Token refresh is not automatic on a 401.** The client raises
   `AliExpressTokenExpiredError` and the service refreshes proactively before
   expiry, but a request that fails mid-flight is not transparently retried with
   a new token. Worth adding when a long-running sync exists to be interrupted.

6. **Redis is required to begin a connection.** By design — the OAuth state has
   nowhere else to live. Three browser tests skip when it is unavailable; the
   backend integration suite covers the same flow with an in-process Redis.

7. **No webhook handling.** AliExpress can push order and shipping updates;
   nothing receives them yet.
