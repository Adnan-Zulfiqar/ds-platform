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

`0031` adds `user_identities`. Additive, with a clean `downgrade()`, and the
upgrade → downgrade → re-upgrade cycle was exercised. **It has not been applied
to production**, which remains at `0029`.

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
