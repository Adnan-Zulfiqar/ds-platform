# Phase 3.6 — Live AliExpress OAuth verification

**Status: complete.** A real OAuth round trip against the live AliExpress Open
Platform now succeeds end to end, and the tokens it returns are stored
encrypted.

This is the first phase whose claims rest on live traffic rather than on
documentation and mocks. Everything below was observed, not inferred.

| | |
|---|---|
| Commit | `fa02dd238b192824ae3aac65ffe11d8da3482ca6` |
| Date | 2026-07-31 |
| Branch | `develop` |
| Gateway | `api-sg.aliexpress.com` |
| Application | app key `541272`, environment `test` |

---

## The defect live traffic found

Phase 3.5 corrected the signing *path*. It could not have found this one,
because nothing had ever been signed against the real gateway.

Every token exchange failed:

```
POST https://api-sg.aliexpress.com/rest/auth/token/create
HTTP 200 OK
code    = IncompleteSignature
message = The request signature does not conform to platform standards
```

Note the HTTP status: **200 OK**. The failure lives in the response body. A
client that branched on status alone would have treated this as success and
stored empty credentials — which is why `_interpret` inspects the body
regardless of status.

`sign_request` excluded `sign_method` from the signature base string, on the
reasoning that the gateway reads it before verifying. That reasoning was wrong.

### How it was isolated without burning authorization codes

An authorization code is single-use and expires quickly, so a guess-and-retry
loop would have cost a fresh browser consent per attempt.

The gateway validates the **signature before the code**. That makes the error
code an oracle: sign a request with a deliberately invalid code, and the
response says which half failed. Four variants, one run, no consent needed:

| Variant | `api_path` prefix | `sign_method` signed | Result |
|---|---|---|---|
| A (shipped code) | `/auth/token/create` | no | `IncompleteSignature` |
| **B** | `/auth/token/create` | **yes** | **`InvalidCode`** ← signature verified |
| C | `/rest/auth/token/create` | yes | `IncompleteSignature` |
| D | *(none)* | yes | `IncompleteSignature` |

Variant B reached code validation, proving the signature was accepted and only
the dummy code refused. So `signing_path_for` was already correct — stripping
`/rest` is right — and the sole defect was the excluded parameter.

**Fix:** `_UNSIGNED_PARAMS` is now `{"sign"}` alone. Only `sign` is exempt,
because it cannot sign itself.

### Verified signature scheme

For REST-style endpoints on this gateway:

```
base = api_path + "".join(f"{k}{v}" for k in sorted(params) if k != "sign")
sign = HMAC_SHA256(app_secret, base).hexdigest().upper()
```

with `api_path` being the URL path minus the `/rest` routing segment,
`timestamp` in milliseconds, and `sign_method=sha256` **included in the base**.
Parameters signed for the token exchange: `app_key`, `code`, `sign_method`,
`timestamp`.

---

## Live OAuth result

Two independent round trips completed, one driven by a scripted harness and one
by a human through the application UI.

```
aliexpress_callback_succeeded   tenant_id=78e74a81-…  (harness)
aliexpress_callback_succeeded   tenant_id=fa7ee5c7-…  (UI)
```

No `aliexpress_api_error` on either.

### Real endpoints exercised

| Endpoint | Result |
|---|---|
| `GET https://api-sg.aliexpress.com/oauth/authorize` | consent screen served, code issued |
| `POST https://api-sg.aliexpress.com/rest/auth/token/create` | HTTP 200, access + refresh tokens returned |

### Application endpoints exercised

| Endpoint | Status |
|---|---|
| `POST /api/v1/auth/register` | 201 |
| `POST /api/v1/integrations/aliexpress/connect` | 201 |
| `GET /api/v1/integrations/aliexpress/callback` | 303 → `?aliexpress=connected` |
| `GET /api/v1/integrations/aliexpress/status` | 200, `connected: true` |

---

## Database verification

No secret, token or ciphertext is reproduced here. Values were checked by
predicate — length, envelope prefix, decryption round trip, and absence of the
known plaintext — and only the verdicts recorded.

```
connection   cb24f0c4-ce98-46c6-93c0-5a8bd3d68035
tenant_id    fa7ee5c7-6de1-4453-846d-91c8fd29102c
status       connected
app_key      541272            (public identifier, stored plaintext by design)
token_expiry 2026-08-30 16:55:24 UTC
last_error   none

connection   b00fdef8-470f-40f7-9ebc-e4cfd7e90e3c
tenant_id    78e74a81-ef06-4d57-9d2d-65ae858546df
status       connected
token_expiry 2026-08-30 16:54:45 UTC
last_error   none
```

| Check | Result |
|---|---|
| Access token stored | 184 bytes ciphertext |
| Refresh token stored | 184 bytes ciphertext |
| Token expiry stored | 30 days out, from the gateway's `expires_in` |
| Fernet envelope (`gAAAAA…`) on all three encrypted columns | pass |
| Each decrypts to a non-empty value | pass |
| Ciphertext differs from plaintext | pass |
| App secret plaintext absent from its column | pass |
| Refresh token **not** stored plaintext | pass |

### OAuth state

| Check | Result |
|---|---|
| Present in Redis before consent, TTL 600s | pass |
| Binds both `tenant_id` and `user_id` server-side | pass |
| **Deleted after the callback** — single use | pass |

The state is 256 bits from `secrets`, held server-side, and consumed on lookup.
Since the callback carries no Bearer token, that state is the only authority on
which tenant owns the flow, so its single-use property is load-bearing rather
than defence in depth.

### Tenant ownership

| Check | Result |
|---|---|
| Every connection carries a `tenant_id` | pass |
| One connection per tenant (4 rows, 4 tenants) | pass |
| Each connection bound to the tenant that began the flow | pass |

---

## Security checks

| Check | Result |
|---|---|
| App secret never logged | pass |
| No access or refresh token appears in the log | pass |
| Encryption keys never logged | pass |
| No authorization code retained in log payloads | pass |
| Tokens encrypted before database storage | pass |
| No credential written to any source file | pass |
| Secrets absent from this document | pass |

Verified by decrypting the stored tokens and searching the full application log
for each plaintext — an assertion about behaviour, not about intent.

---

## Tests

| Gate | Result |
|---|---|
| `pytest tests/unit` | 216 passed |
| `pytest tests/integration` | 60 passed |
| `ruff check` / `ruff format --check` | clean, 93 files |
| `mypy app` | clean, 77 files |

`test_sign_method_is_part_of_the_signature` is the regression guard. It exists
because the gateway's error names neither the parameter nor the scheme: without
it, re-excluding `sign_method` would produce a green suite and a dead
integration.

---

## Remaining limitations

1. **Only the token exchange has been exercised live.** No business API call has
   been made. Product search, detail and order endpoints remain unverified, and
   their request shapes are still taken from documentation.

2. ~~**The consent scope looks too narrow for Phase 4.**~~ **Withdrawn — this
   was wrong.** The authorization screen requested only *Alibaba Member's Basic
   Information*, and this report concluded that was the most likely blocker for
   Phase 4. Phase 3.7 probed the live APIs directly and found the dropship
   endpoints fully accessible with the token that consent produced.

   The consent screen describes what is read from the end user's *account*. It
   does not enumerate the application's API permission groups, which is what
   governs callable methods. Inferring API access from the consent screen was
   the error. See [PHASE_3_7_PERMISSIONS.md](PHASE_3_7_PERMISSIONS.md).

3. **Token refresh is untested against the live gateway.** `refresh_if_needed`
   and the refresh endpoint signing are exercised only by mocks. The stored
   tokens expire 2026-08-30, so this cannot be observed naturally before then.

4. **The application is registered as 【AutoPilot】, not DropPilot AI.** That is
   the name shown to every seller on the consent screen.

5. **The callback reaches the backend through a Cloudflare Tunnel forwarding a
   single path.** Suitable for development only. Every other route on
   `api.whiteto.com` returns 404, which is sufficient because AliExpress calls
   nothing else, but it is not a deployment.

6. **Docker remains unbuilt** (C1 in `TECHNICAL_DEBT.md`). All verification ran
   against natively installed PostgreSQL and Redis.

7. **Local Redis is 3.0.504**, old enough to reject RESP3, which is why the
   client is pinned to RESP2. Production is intended to run Redis 7.

---

## Files changed

| File | Change |
|---|---|
| `backend/app/integrations/aliexpress/auth.py` | `sign_method` included in the signature base; scheme documented as verified |
| `backend/tests/unit/test_aliexpress_client.py` | Replaced the test asserting the old behaviour; added the regression guard |
