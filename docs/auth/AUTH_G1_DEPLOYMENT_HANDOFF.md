# AUTH-G1 — Google sign-in and Resend, deployment handoff

Everything below is configuration and portal work. **None of it was performed
during implementation**, and no live Google sign-in or real email send has ever
been executed against this code — see "What has and has not been proven".

---

## Google

### What already exists (confirmed by the operator)

* Google Cloud project **DropPilot AI**
* A **Web** OAuth client for `https://app.whiteto.com`
* The app is in **Testing** mode

### What the application needs

| Setting | Value |
|---|---|
| Authorized JavaScript origin | `https://app.whiteto.com` |
| Authorized redirect URI | **none** — the popup credential flow uses no redirect |
| Scopes | `openid email profile`, fixed in code |
| Client secret | **not used, do not supply one** |

The popup credential flow exchanges no authorization code, so there is no
confidential credential. If a secret has been generated for this client it plays
no part here and should not be copied anywhere.

For local development the origin must also include the dev host
(`http://localhost:3000`), otherwise Google refuses to render the button.

### Environment

```
GOOGLE_OAUTH_CLIENT_ID=<the web client id>
```

and, **built into the frontend** (`NEXT_PUBLIC_*` is inlined at build time, so
setting it afterwards does nothing):

```
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<the same web client id>
```

Leaving either blank means the button is not rendered at all. That is the
intended behaviour: an absent button is better than a broken one.

### Still outstanding

**Testing mode allows only listed test users to sign in.** Moving to Production
requires Google's verification, which reviews the consent screen, the app name
and the privacy policy URL — which is why `/privacy` had to exist first
(EBAY-C1.1). Recorded as launch blocker 7.

---

## Resend

### What already exists

* Sending domain **`auth.whiteto.com`**, verified, region **Ireland
  (`eu-west-1`)**

### What is missing

**The API key has not been created.** Nothing in this milestone created one, and
`EMAIL_PROVIDER` defaults to `stub`, so the deployed application currently sends
no email at all.

### Environment

```
EMAIL_PROVIDER=resend                                   # `stub` until ready
EMAIL_FROM=DropPilot AI <security@auth.whiteto.com>
EMAIL_REPLY_TO=privacy@whiteto.com
RESEND_API_KEY=<create in the Resend dashboard>
```

Switching `EMAIL_PROVIDER` to `resend` without a key makes the application fail
closed at startup of the first send rather than silently not sending.

### Guards worth knowing about

* The integration refuses any base URL that is not `api.resend.com`. An
  integration that can be redirected can be made to post customer addresses
  somewhere else.
* `EMAIL_MAX_SENDS_PER_HOUR` is an application-side ceiling, and the circuit
  breaker opens after `EMAIL_BREAKER_FAILURE_THRESHOLD` consecutive failures.
  Both fail closed. They exist so a loop or an abuse burst cannot quietly
  consume the provider quota.
* Nothing logs the recipient, the provider response body, or the key.

### Still outstanding

**No Data Processing Agreement has been signed with Resend**, and no transfer
assessment has been completed. Recorded as launch blocker 8.

---

## The OTP secret

```
SECURITY_OTP_HMAC_KEY=<long random value>
```

**Mandatory in production, and it must differ from `SECURITY_SECRET_KEY`.**

A six-digit code has a million possibilities. A stored SHA-256 of one is a table
lookup, so codes are kept as a domain-separated HMAC under this key. Sharing the
JWT signing key would mean one leak compromised both, and would force rotating
either for the other's sake.

Rotating this key invalidates every outstanding reset code and ticket. That is
harmless — they expire in ten minutes anyway — but it will strand anybody
mid-flow, so rotate at a quiet moment.

---

## Migration

`0031` adds `user_identities`. `0032` adds the four registration acceptance
columns. Both are additive, both have a clean `downgrade()`, and the
`0030 → 0031 → 0032 → downgrade → re-upgrade` cycle was exercised. **Neither has
been applied to production**, which remains at `0029`.

---

## What AUTH-G1-R1 changed for deployment

### One endpoint became five

The combined `POST /auth/google` is gone. An endpoint that decided between
signing in and registering from the shape of the request could create a
workspace for somebody who meant to sign in, and the nonce it consumed did not
say which operation it had been minted for.

| Endpoint | Who may call it |
|---|---|
| `POST /auth/google/nonce` | anyone; body names `login` or `signup` |
| `POST /auth/google/link/nonce` | signed in; bound to that user and tenant |
| `POST /auth/google/login` | anyone; **creates nothing** |
| `POST /auth/google/signup` | anyone; requires acceptance |
| `POST /auth/google/link` | signed in; requires the account password |
| `POST /auth/google/unlink` | signed in; requires the account password |

A nonce is single-use and bound to its intent, so one minted for `login` is
refused by `signup` and by `link`.

### `POST /auth/register` now requires acceptance — a breaking change

Registration records which documents the account holder agreed to, so the
request must carry `termsAccepted`, `privacyAccepted`, `termsVersion` and
`privacyVersion`, and the server refuses a false flag or a version it does not
recognise. **Any existing client that posts to `/auth/register` without them now
receives a 400 `legal_acceptance_required`.** There is no compatibility window:
accepting a signup with no record of consent is the exact thing this change
exists to prevent, and the only callers today are this repository's own
frontend and its test suite.

The versions live in `backend/app/core/legal.py` and `frontend/lib/legal.ts` and
must move together. `TERMS_VERSION` is the sentinel `"unpublished"`, because no
Terms document exists — see launch blocker below.

### `SECURITY_OTP_HMAC_KEY` is now refused rather than defaulted

A deployed environment (anything but `local`) will not start with the key
absent, left at its insecure default, or set equal to `SECURITY_SECRET_KEY`.
`EMAIL_PROVIDER=resend` without `RESEND_API_KEY` is refused at the same point.
Both were previously survivable misconfigurations that failed later, quietly.

### Content-Security-Policy

The frontend sends its own CSP, built per request in `middleware.ts` from
`lib/csp.ts`. Google Identity Services needs four origins and gets exactly
those, with no wildcard on `google.com`:

```
script-src  'self' 'nonce-<per response>' https://accounts.google.com
style-src   'self' 'unsafe-inline'        https://accounts.google.com
frame-src                                 https://accounts.google.com
connect-src 'self' <API origin>           https://accounts.google.com
img-src     'self' data:                  https://*.googleusercontent.com
```

`style-src` was missing from the first cut and the browser blocked
`accounts.google.com/gsi/style`, which would have drawn Google's button
unstyled. It was caught by a console-error assertion in the wider end-to-end
run, not by reading the header.

**`script-src` carries no `'unsafe-inline'`.** That keyword permits every inline
script on the page, which is exactly the capability an XSS payload needs — it
makes the strongest directive in the policy a no-op. A fresh 128-bit nonce is
minted from Web Crypto for each response instead; Next.js reads the policy from
the *request* header, finds the `'nonce-…'` token and stamps it onto every
script tag it renders. `next-themes` is handed the same nonce explicitly,
because Next.js cannot reach into a library's own inline script.

`'unsafe-inline'` remains on **styles**. Next.js injects critical CSS inline and
offers no nonce for it. Inline CSS is not script execution, and the exchange is
a narrow style risk for the removal of the script one.

#### What the nonce costs

**Every page now renders per request.** A page prerendered at build time has its
script tags written long before the nonce exists, so they would arrive without
one and be refused. `app/layout.tsx` reads a request header, which opts the
whole tree into dynamic rendering; the build output shows every route as `ƒ`
rather than `○`.

The bill is small here and is stated rather than discovered later: every route
below `/` is an authenticated, user-specific dashboard that was already dynamic,
and the three public pages (`/login`, `/register`, `/privacy`) are static markup
with no data fetching. Downstream HTTP caching is unchanged — protected routes
already send `no-store`, public ones are untouched — but **a shared cache must
never store these responses keyed without the header**, because two visitors
would then share one nonce. Nginx must not add caching for HTML on this origin.

Nginx sets the other security headers at the edge as well; this is defence in
depth for a direct-to-Node deployment.

### Step-up throttling

Linking and unlinking Google require the account password. That step-up is now
rate limited, sharing one counter across both operations — separate counters
would mean twice the guesses for anybody willing to alternate.

```
SECURITY_STEP_UP_MAX_ATTEMPTS=3            # per user and per address
SECURITY_STEP_UP_ATTEMPT_WINDOW_SECONDS=300
SECURITY_STEP_UP_LOCKOUT_SECONDS=900
```

Two things differ from the login throttle deliberately:

* The attempt is **counted before** the password is checked, not after. Reading
  a counter, verifying, then recording a failure lets twenty simultaneous
  requests all read zero — twenty guesses inside a limit of three.
* Redis being unavailable **refuses** the operation rather than allowing it.
  Login fails open, because locking every customer out of the product is worse
  than a window of unthrottled sign-in attempts. That trade does not carry over
  to an operation that attaches a permanent second way into an account.

Counters are keyed on a hash of tenant-and-user and a hash of the address —
never an email — so a Redis dump is not a customer list.

### Password reset under contention

`verify` uses optimistic locking, and its retry is bounded at eight attempts
with jittered backoff. An unbounded loop terminates only because conflicts are
rare, which is an observation about typical load rather than a property of the
algorithm. On exhaustion the caller gets `503 password_reset_busy`: no attempt
was spent, no ticket was issued, and the challenge is untouched, so a later try
still works. It is deliberately *not* reported as a wrong code — that would burn
a guess the person never made.

### Still outstanding

**No Terms of Service document exists.** Registration records
`terms_version = "unpublished"` and the form says so in as many words. Nobody
should read a stored acceptance row as agreement to terms that have never been
written. Publishing them, setting a real version in both `legal` modules and
re-prompting existing accounts is a launch blocker.

---

## What has and has not been proven

**Proven, in an isolated stack:**

* Credential verification, including rejection of a bad issuer, wrong audience,
  unverified email, absent subject, expired token and replayed nonce — with only
  Google's own library call mocked, so every check this application makes ran
  for real.
* Sign-up, sign-in, the refusal to auto-link a matching local address, the
  cross-tenant subject conflict, and unlink refusal for a passwordless account.
* The full reset flow: HMAC-at-rest, expiry, attempt ceiling, cooldown, hourly
  limits, single active challenge, replay refusal, concurrent verification
  admitting exactly one, session revocation, and password policy reuse.
* Resend refusing a foreign host, refusing a missing key, failing closed on an
  unparseable response, and never putting a recipient in an error.

**Not proven, and not claimed:**

* **No live Google sign-in has been performed.** No real credential has ever
  been verified against Google's servers by this code.
* **No real email has been sent.** Every test used the recording stub or a mock
  HTTP transport. No Resend API key exists.
* The Google button has not been rendered by Google's real script — the
  Playwright tests stub `accounts.google.com/gsi/client`, because loading it for
  real would contact Google.

The first live sign-in and the first real send are therefore **still
unverified**, and should be treated as the first genuine test of this
integration rather than a formality.
