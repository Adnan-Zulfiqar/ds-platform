# Authentication and tenant identity

How DropPilot AI establishes **who** a caller is, **which tenant** they act
within, and **what** they may do — and why each decision was made that way.

Introduced in Phase 1, replacing the Phase 0 `X-Tenant-ID` header placeholder.

## Contents

1. [The resolution chain](#the-resolution-chain)
2. [Token design](#token-design)
3. [Refresh token rotation](#refresh-token-rotation)
4. [Password storage](#password-storage)
5. [Login throttling](#login-throttling)
6. [Roles and authorization](#roles-and-authorization)
7. [Frontend session handling](#frontend-session-handling)
8. [Security decisions and their trade-offs](#security-decisions-and-their-trade-offs)
9. [Known limitations](#known-limitations)

---

## The resolution chain

```
HTTP request
  └─ Authorization: Bearer <access token>
       └─ signature, issuer, audience, expiry, and type verified   (core/tokens.py)
            └─ AuthenticatedUser value object                       (core/context.py)
                 └─ bound to contextvars: principal + tenant + user
                      └─ TenantScopedRepository injects tenant_id into every query
```

The critical property: **the tenant is derived from a signed claim, never from
client-supplied input.** In Phase 0 it came from a header any caller could set —
useful for exercising the machinery, useless as access control. The verification
step is what changed; nothing downstream did.

That is why the Phase 1 change was contained. Endpoints, services, and
repositories already depended on bound context rather than on the header, so
only the body of the resolver in `app/api/deps.py` was replaced.

---

## Token design

Two token types with different jobs. Both are JWTs; both carry a `typ` claim
that is **verified on every decode**.

| | Access token | Refresh token |
|---|---|---|
| Lifetime | 15 minutes | 30 days |
| Sent on | Every API request | `/auth/refresh` and `/auth/logout` only |
| Transport | `Authorization` header | httpOnly cookie (or request body) |
| Claims | `sub`, `tid`, `roles`, `jti`, `iat`, `exp`, `iss`, `aud`, `typ` | Same, without `roles` |
| Revocable | **No** | **Yes** — SHA-256 hash stored in `refresh_tokens` |
| Stored server-side | No | Hash only |

### Why the `typ` claim is verified

Without it, a refresh token would be accepted as an access token. That single
omission turns a 30-day credential into a permanent API key and defeats rotation
entirely. `tests/unit/test_tokens.py` covers both directions.

### Why access tokens are not revocable

Checking a revocation list on every request puts a database or cache read in
front of every endpoint, which removes the reason to use stateless tokens at
all. The mitigation is the short lifetime: 15 minutes is the maximum window in
which a stolen access token works.

Where that window is unacceptable — deleting a user, changing a password —
revoke the refresh tokens too. The session then dies at the next refresh.

### Why roles are embedded in the access token

Authorization needs no database read on the hot path. The cost is bounded
staleness: a role revoked mid-session remains effective until the access token
expires. Refresh re-reads roles from the database, so the window never exceeds
one access-token lifetime.

`GET /auth/me` deliberately re-reads roles from the database rather than echoing
the token, so a UI that hides an admin control hides it immediately.

### Why HS256

One service both issues and verifies tokens, so there is no party that needs to
verify without also being able to sign. Move to RS256 when that stops being
true — a separate auth service, an edge gateway, or a third-party verifier —
because sharing a symmetric secret with any of them would let them mint tokens.

The signing key must be **at least 32 characters** (RFC 7518 §3.2 for HS256).
The application refuses to start below that in every environment, because a
short key signs and verifies perfectly well and fails only silently.

---

## Refresh token rotation

Every refresh consumes the presented token and issues a new one.

```
client                                  server
  │  POST /auth/refresh  (token A)        │
  │ ─────────────────────────────────────►│  verify A, mark A revoked
  │                                       │  issue B
  │ ◄───────────────────────────────────── │
  │  token B                              │
```

### Reuse detection

Presenting a token that is already revoked means two parties hold it — the
legitimate client and someone who captured it. There is no way to tell which is
which, so **every session for that user is terminated** and both must sign in
again.

Briefly locking out the real user is a far better outcome than leaving an
attacker with a live session. Covered by
`tests/integration/test_auth_flows.py::TestTokenRotation`.

### Why the stored hash is SHA-256, not Argon2

A refresh token is 256 bits of cryptographic randomness with no guessable
structure. Slow, memory-hard hashing exists to make low-entropy human passwords
expensive to brute-force; against a random 256-bit value it buys nothing and
would add real latency to a call made on every session renewal.

---

## Password storage

**Argon2id**, via `argon2-cffi`, with OWASP-recommended parameters (19 MiB,
2 iterations, 1 lane — configurable).

Chosen over bcrypt because it is memory-hard: an attacker with GPUs or custom
silicon gains far less advantage, since memory bandwidth does not parallelise
the way raw compute does. It also has no 72-byte truncation limit, so a long
passphrase is hashed in full rather than silently cut short.

Three details that are easy to miss:

* **NFKC normalisation.** A password typed with a composed accent and one typed
  with a combining accent are different byte sequences. Without normalising,
  a user who switches keyboard or platform is locked out with no visible cause.
* **Transparent rehashing.** Parameters are raised as hardware improves. The
  plaintext is only in memory during a successful login, so that is the single
  opportunity to upgrade a stored hash — and it is taken.
* **Constant-cost failure.** When no account matches, a dummy Argon2
  verification runs anyway. Returning early would make unknown addresses
  measurably faster to reject and turn the login form into an enumeration
  oracle.

### Password policy

Minimum 12 characters, requiring upper case, lower case, and a digit. Symbols
are *not* required by default: forcing them pushes users towards predictable
substitutions (`Password1!`) without materially raising entropy, whereas length
dominates real-world strength.

A short denylist rejects the most common passwords, and a password containing
the local part of the user's own email address is refused.

---

## Login throttling

`/auth/login` is unauthenticated, accepts guessable input, and is the highest
value target in the application. It is throttled far more strictly than the
general API: **5 attempts per 5 minutes, then a 15-minute lockout.**

Counted independently against **both** the email address and the client IP,
because either alone is insufficient:

* IP alone lets an attacker spread one password across thousands of accounts
  from one address without tripping a per-account limit — and punishes everyone
  behind a shared NAT when one person mistypes.
* Email alone lets an attacker lock a known user out of their own account by
  deliberately failing their login: a denial-of-service disguised as a security
  control.

Counters are cleared on success, and the check runs **before** password
verification, so a locked-out caller never reaches the expensive Argon2
comparison.

Email addresses are hashed before being used as Redis keys. Keys appear in
`MONITOR` output and support dumps, and a keyspace full of customer addresses is
a personal-data leak waiting to be exported.

---

## Roles and authorization

Four platform-global roles, seeded by migration `0002`:

| Role | Rank | Intent |
|---|---|---|
| `owner` | 30 | Billing control; cannot be removed from a tenant |
| `admin` | 20 | Full operational access |
| `member` | 10 | Day-to-day operations |
| `viewer` | 0 | Read-only |

Roles are global rather than per-tenant so authorization is a name comparison
and signup does not seed four rows per tenant. When custom roles are needed, a
nullable `tenant_id` on `roles` lets global and tenant-defined roles coexist
without migrating existing rows.

Two dependency factories enforce them:

* `require_roles(ADMIN)` — exact membership.
* `require_minimum_role(ADMIN)` — the role *or any that outranks it*. Prefer
  this for hierarchical checks; listing `ADMIN, OWNER` explicitly means a role
  inserted above admin later would silently fail to gain access it should have.

Both are dependencies rather than in-handler checks, so they run before the
handler body and appear in the OpenAPI document. An authorization check buried
in a function body is easy to omit when the next endpoint is copied from it.

---

## Frontend session handling

| Credential | Where it lives | Why |
|---|---|---|
| Access token | **In-memory only** (module variable) | Never in `localStorage` — anything there is readable by any script, so one XSS payload exfiltrates a working credential |
| Refresh token | **httpOnly cookie**, path-scoped to `/api/v1/auth` | Invisible to JavaScript; not attached to ordinary API calls |

On boot the app calls `/auth/refresh` once. The in-memory access token is gone
after a reload, but the cookie survives, so the session is restored without the
user retyping anything. The credential that persists is the one JavaScript
cannot touch; the one JavaScript holds expires in minutes.

### Single-flight refresh

When the access token expires, a dashboard firing several requests at once gets
several simultaneous 401s. Without a guard each would trigger its own refresh —
and because refresh tokens rotate, the first would consume the token and the
rest would present a consumed one, which the server correctly treats as theft
and responds to by terminating every session.

**Omitting the single-flight guard does not merely waste requests: it signs the
user out.** `lib/api-client.ts` holds one shared in-flight promise.

### Route protection is a UX gate, not a security boundary

The security boundary is the API. Every endpoint verifies a signed token
server-side, so bypassing the client guard reveals an empty shell that cannot
load data.

`middleware.ts` deliberately does *not* gate on the session cookie: the cookie
is path-scoped to the API and, in local development, served from a different
origin, so middleware cannot reliably see it. Gating there would produce
redirect loops. `components/auth-guard.tsx` performs the real client-side check,
and it distinguishes *loading* from *unauthenticated* — conflating the two
throws authenticated users out on every page refresh.

---

## Security decisions and their trade-offs

| Decision | Benefit | Accepted cost |
|---|---|---|
| Stateless access tokens | No database read per request | Cannot revoke before expiry (bounded to 15 min) |
| Roles in token claims | No query for authorization | Role changes lag by up to one access lifetime |
| Refresh rotation + reuse detection | Captured tokens have a short useful life | A genuine client bug can log a user out |
| Uniform login failure message | No account enumeration | Users get less specific feedback |
| Login throttle fails open on Redis outage | An infrastructure failure does not lock out every customer | No throttling while Redis is down — needs its own alerting |
| httpOnly refresh cookie | XSS cannot steal the long-lived credential | Requires CORS credentials and careful cookie attributes |
| Argon2id over bcrypt | Memory-hard; no truncation | Higher CPU and memory per login |

---

## Known limitations

1. **Password reset is not implemented.** It needs email delivery, a signed
   single-use token, and its own expiry policy. The page exists and says so
   plainly rather than faking a confirmation email — a locked-out user told to
   check their inbox will wait instead of contacting support.

2. **Email verification is not implemented.** `is_verified` is set to `true` on
   registration because there is no mail delivery to verify against. When the
   flow lands, that default becomes `false`.

3. **Registration discloses that an address is already taken.** Unavoidable
   without email delivery — the account is either creatable or not. The fix is
   to always report success and email the existing account holder instead.

4. **One email address across two tenants resolves to the earliest account.**
   `users` is unique on `(tenant_id, email)`, so the same person may hold
   accounts in two tenants. Login tests the password against each candidate, but
   if the password is genuinely reused the first-created account wins and the
   other is unreachable from the login form. The fix is tenant-qualified sign-in
   (subdomain, or a tenant picker after the password is verified).

5. **No breached-password check.** A corpus check catches far more real
   compromise than any composition rule. A k-anonymity range query against Have
   I Been Pwned, or a local corpus, should be added before live customer
   accounts exist.

6. **No multi-factor authentication.** The token and session machinery is
   structured to accommodate it — MFA becomes a step between password
   verification and token issue in `AuthService.login`.

7. **No CSRF token.** The refresh cookie is `SameSite=Lax` and path-scoped, and
   the only cookie-authenticated endpoints are `/auth/refresh` and
   `/auth/logout`, neither of which performs a damaging state change. A
   double-submit token should be added if cookie authentication is ever extended
   to mutating endpoints.

8. **Sessions are not listed or individually revocable by the user.**
   `logout-all` exists; a per-device session list does not.
