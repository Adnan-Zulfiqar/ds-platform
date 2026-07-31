# AliExpress integration

How a tenant connects their AliExpress account, how their credentials are
protected, and why each decision was made that way.

> ## ⚠️ Verification status
>
> A **documentation review** was completed in Phase 3.5 and found and fixed one
> genuine defect (the signing path prefix — see
> [Verification log](#verification-log)).
>
> **No request has ever been sent to AliExpress.** Everything below is verified
> against documentation and reference implementations, not against the live
> gateway.
>
> Two infrastructure fixes cleared the remaining blockers:
>
> * **Callback routing** — registered at
>   `https://api.whiteto.com/api/v1/integrations/aliexpress/callback`, forwarded
>   to the local backend by a Cloudflare Tunnel.
> * **Webhook routing** — register
>   `https://api.whiteto.com/api/v1/integrations/aliexpress/webhook` separately.
>   The tunnel must forward this path as well; it is not the OAuth callback.
> * **Redis protocol** — local Redis ports that reject RESP3's `HELLO` command
>   now connect with RESP2. Without that, the OAuth `state` store and rate
>   limiter failed on every request.
> * **Callback authentication** — the callback no longer requires a Bearer
>   token. The browser arrives from AliExpress without one; tenant binding comes
>   from the server-side OAuth `state` issued during `/connect`.
>
> **Still unverified against the live gateway.** Use
> `backend/scripts/verify_aliexpress.py` or the application UI to complete the
> first live OAuth round trip. See [Running live verification](#running-live-verification).

## Contents

1. [Architecture](#architecture)
2. [OAuth flow](#oauth-flow)
3. [Webhook flow](#webhook-flow)
4. [Credential handling](#credential-handling)
5. [Security model](#security-model)
6. [API client](#api-client)
7. [Rate limiting](#rate-limiting)
8. [Background tasks](#background-tasks)
9. [Configuration](#configuration)
10. [Correcting the contract](#correcting-the-contract)
11. [Known limitations](#known-limitations)

---

## Architecture

```
app/integrations/aliexpress/
├── exceptions.py   typed failures, each declaring whether it is retryable
├── schemas.py      wire models (theirs) and API models (ours)
├── auth.py         request signing + OAuth state
├── webhook.py      inbound push notifications (separate from OAuth callback)
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

### Why the callback does not require a Bearer token

AliExpress redirects the user's browser here after consent. That navigation
carries no `Authorization` header — only the `code` and `state` query
parameters. Requiring a Bearer token would make every real OAuth round trip fail
with 401.

The CSRF defence does not depend on session authentication on return. During
`/connect`, an authenticated admin stores credentials and receives a random
`state` token bound to their tenant in Redis. On callback, that token is looked
up, consumed, and used to bind tenant context before the code is exchanged.
An attacker who does not know the state cannot attach a connection to a victim's
workspace.

### Why the callback redirects instead of returning JSON

The browser arrives directly from AliExpress's consent screen, so the user must
end up on a page. Failures redirect too, carrying a short reason
(`?aliexpress=denied|failed|invalid`) drawn from a fixed vocabulary this
application controls — never an upstream message, which could be reflected into
the page.

---

## Webhook flow

AliExpress can push order and shipping notifications. These are **not** OAuth
callbacks — they arrive as `POST` requests with a JSON or form-encoded body.

```
POST /api/v1/integrations/aliexpress/webhook
      ├─ parse body (JSON or form-urlencoded)
      ├─ log payload for inspection
      └─ return HTTP 200 {"status": "received"} immediately
```

### Why this is separate from the OAuth callback

| | OAuth callback | Webhook |
|---|---|---|
| Method | `GET` | `POST` |
| Caller | User's browser after consent | AliExpress servers |
| Purpose | Exchange an authorization code | Push order/shipping events |
| Response | `303` redirect into the app | `200` JSON acknowledgement |

Reusing the callback URL would conflate two different contracts and break one of
them the first time AliExpress POSTs while the browser expects a redirect.

### Registration URL

Configure this in the AliExpress developer console (not the OAuth redirect URI):

```
https://api.whiteto.com/api/v1/integrations/aliexpress/webhook
```

The Cloudflare Tunnel must forward this path to the local backend, the same way
it forwards the OAuth callback. A request that never reaches this application
returns 404 from the tunnel edge.

### Security status

**Signature verification is not implemented yet.** The handler accepts any POST,
logs the payload, and acknowledges receipt. That is acceptable only while no
business logic runs here. Before processing order or customer data, confirm
AliExpress's webhook signing scheme and implement verification in
`app/integrations/aliexpress/webhook.py`.

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

## Verification log

### Phase 3.5 — documentation review, 2026-07-31

Compared against the AliExpress Open Platform documentation and two independent
reference implementations. Note the official doc portals
(`openservice.aliexpress.com`) are JavaScript-rendered and could not be read
programmatically, so reference implementations carried most of the weight.

| Item | Result |
|---|---|
| Authorization URL and parameters | ✅ Matches |
| Signature algorithm (HMAC-SHA256, upper hex) | ✅ Matches |
| Parameter sorting | ✅ Matches |
| `sign_method` excluded from the base string | ✅ Matches |
| **API path prefix in the signature** | ❌ **Defect found and fixed** |
| Token and refresh endpoint paths | ✅ Matches |
| Token response field names | ✅ Plausible; `extra="allow"` tolerates variation |
| Error code mappings | ⚠️ Unverified — needs live failures to confirm |

#### The defect

REST-style endpoints prefix the signature base string with the API path, minus
the `/rest` routing segment. TOP-style requests to `/sync` prefix nothing —
the method travels as a parameter instead.

`AliExpressClient.call` handled this correctly, but `exchange_token` signed
**without** the prefix. Token creation and refresh would therefore have been
rejected with an invalid-signature error — at the exact moment a user finished
authorising, with nothing in the message indicating why.

Fixed by deriving the prefix from the URL in `auth.signing_path_for`, so no call
site has to remember it. Pinned by a regression test that recomputes the
signature over exactly what was transmitted and asserts it is *not* the
unprefixed form.

#### One observation worth noting

A live authorization URL seen in the wild carried `redirect_auth=true` rather
than `redirect_uri=...`. Reference implementations use `redirect_uri`, and that
is what is implemented. If authorization fails with a redirect-related error,
this is the first thing to check.

---

## Running live verification

`backend/scripts/verify_aliexpress.py` walks the flow against the real gateway.
**Credentials come from the environment and are never printed** — the script
masks every secret and reports response *shapes* rather than contents, so a
token cannot end up in a scrollback buffer or a screen recording.

```bash
# Configuration only — contacts nothing.
python scripts/verify_aliexpress.py preflight
```

```bash
# Print the authorization URL to open in a browser.
python scripts/verify_aliexpress.py authorize
```

```bash
# Exchange the code from the redirect. It is single-use and expires quickly.
ALIEXPRESS_AUTH_CODE=... python scripts/verify_aliexpress.py exchange
```

```bash
# One read-only call.
ALIEXPRESS_ACCESS_TOKEN=... python scripts/verify_aliexpress.py call
```

### Prerequisites

1. **`ALIEXPRESS_APP_KEY` and `ALIEXPRESS_APP_SECRET`** — configured, and
   confirmed to load. Note that until the `_EnvFileSettings` fix, a value
   present in `.env` was read by the root settings only: every nested group,
   this one included, ignored the file entirely and reported the credentials
   missing. If credentials ever appear absent despite being set, check that
   first.
2. **The callback must match the developer console exactly, and be reachable.**
   Registered and configured as
   `https://api.whiteto.com/api/v1/integrations/aliexpress/callback`, forwarded
   to the local backend by a Cloudflare Tunnel.

   Only that one path is forwarded. `/health`, `/docs` and every other route
   return 404 on `api.whiteto.com` and never reach this machine. That is
   sufficient — AliExpress calls nothing else — but it means the subdomain is
   not a general-purpose route to the backend.

   Set `ALIEXPRESS_CALLBACK_URL`, not `ALIEXPRESS_REDIRECT_URI`. Both are
   aliases for one field and the former wins, so a value set only in the latter
   is read and discarded.

   For the script alone, the redirect only has to be *reachable enough* for the
   browser to land somewhere: the code is in the URL and usable even if the page
   errors. For the **application** flow to work end to end, the callback must
   genuinely reach this backend.
3. **Redis must be running** for the application flow — the OAuth `state` lives
   there and the service fails closed without it. The script does not need it.

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

7. **Webhook processing is stubbed.** The endpoint receives and logs payloads but
   does not yet verify signatures or update orders. See [Webhook flow](#webhook-flow).
