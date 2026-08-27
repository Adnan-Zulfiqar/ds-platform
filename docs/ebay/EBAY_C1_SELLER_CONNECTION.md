# EBAY-C1 — seller OAuth connection

**Status: complete.** The "Coming soon" eBay placeholder is gone; the
integrations page now carries a working card that connects, reports, reconnects
and disconnects a real eBay seller account.

What EBAY-C0 delivered was compliance: an endpoint eBay could validate and a
ledger proving deletions were honoured. It stored nothing. C1 is the first
milestone that stores eBay personal data, which is why half of this document is
about how that data is protected and erased.

---

## What it does

| Endpoint | Who | What |
|---|---|---|
| `GET /api/v1/integrations/ebay/status` | any authenticated role | Whether eBay is configured on this server, whether this workspace is connected, and the connection's display state |
| `POST /api/v1/integrations/ebay/connect` | admin or owner | Returns the eBay consent URL for this workspace |
| `GET /api/v1/integrations/ebay/callback` | public | Completes consent and redirects the browser back into the application |
| `DELETE /api/v1/integrations/ebay/disconnect` | admin or owner | Removes the connection and its stored credentials |

One eBay seller account per workspace, and one workspace per eBay seller
account. Both directions are enforced by the database.

---

## The decisions worth recording

### The consent URL is pinned, because it cannot be debugged

A wrong endpoint, a wrong `redirect_uri` or an invented scope produces a failure
on eBay's own page — where there is no log to read and no error body to inspect.
So every part of it is asserted character for character in
`tests/unit/test_ebay_c1_oauth.py`, and every literal there was read from eBay's
published documentation and OpenAPI specifications rather than from memory.

Three details that are easy to get wrong and expensive to diagnose:

* **`auth.ebay.com`, not `api.ebay.com`.** Consent is hosted on a different host
  from every API call. Pointing at the API host yields a page that is not a
  consent screen.
* **`redirect_uri` is not a URL.** It carries the **RuName**, an opaque
  identifier the portal issues for a redirect URL set. eBay resolves it to the
  accept and decline URLs configured against it.
* **The Identity API lives on `apiz.ebay.com`.** A genuine eBay quirk, and the
  source of a 404 that reads like a permissions error if it is got wrong.

### The scope set is the smallest one that does the job

```
https://api.ebay.com/oauth/api_scope
https://api.ebay.com/oauth/api_scope/commerce.identity.readonly
https://api.ebay.com/oauth/api_scope/sell.account
https://api.ebay.com/oauth/api_scope/sell.inventory
https://api.ebay.com/oauth/api_scope/sell.fulfillment
```

What is deliberately **absent** matters as much. No `sell.finances`, no
`sell.payment.dispute`, no `sell.marketing`, no `sell.advertising`. Each exists
and each would widen the consent screen — and a merchant who is asked for access
to their money is right to refuse.

`commerce.identity.readonly` alone returns the account id without the person.
The email, name, address and phone variants would each pull personal data this
platform has no use for, and would then have to be declared under the C0 storage
contract.

The scopes for C2–C5 are requested now rather than later because eBay's consent
screen is per-authorization: adding a scope in C3 would mean sending every
connected merchant back through consent. Asking once for the audited set is the
smaller imposition. Requesting scopes for work that does not exist yet is the
trade-off, and it is stated here rather than left for someone to discover.

### The identity is eBay's immutable `userId`, never the username

eBay's specification says of `userId`: *"can always be used to identify the
user"*, and of `username`: *"This value can be changed by the user."*

Keying on the name would mean one rename makes the same seller look like a new
account. Reconnecting would create a second row, orphan the first, and — the
part that matters — the deletion contract would no longer find what it must
erase. The username is stored for display only and is refreshed on every verify.

### One seller cannot be linked to two workspaces, and asking reveals nothing

Enforced by a **global** unique constraint on `ebay_user_id`, not by a lookup.
Two reasons, and the second is the important one:

1. A check-then-insert races. The constraint does not.
2. A cross-tenant lookup would be an existence oracle. A caller could learn
   whether a given eBay seller uses DropPilot by watching which error came back.

So `EbayConnectionRepository` has no `get_by_ebay_user_id`, the conflict surfaces
only as an integrity violation on insert, and the message says nothing about the
workspace that holds the account. That is the same reasoning behind
404-not-403 for cross-tenant reads elsewhere in the platform.

### State is single-use, server-side, and consumed before the code is spent

The callback trusts the state record and nothing else. eBay's redirect carries
only `code` and `state`; which workspace this belongs to, which admin started it
and which eBay estate it targets all come from the Redis record that `state`
unlocks. A tenant id read from a URL is a tenant id an attacker can type.

Only the SHA-256 of the state token is used as the Redis key, so a leaked Redis
snapshot contains no usable state credential.

The state is deleted **before** the authorization code is exchanged. Exchanging
first and deleting afterwards leaves a window in which a replayed callback runs
the exchange twice.

### Refresh is serialised by a database row lock

Two requests that both notice an expiring token would otherwise both call eBay,
and the loser would overwrite the winner's token with an older one — while
spending the daily refresh quota twice as fast.

`SELECT … FOR UPDATE` is taken first and the expiry re-read afterwards, so
whichever request loses the race finds the token already renewed and returns it.

This is tested against real contention rather than hoped for: the first caller is
held inside eBay's token call after it has the lock, and the test waits for
PostgreSQL's own `pg_blocking_pids()` to report the second caller blocked. A
companion test removes the lock and demonstrates the duplicate refresh it
prevents — without that control, a green serialisation test could just mean the
two callers never overlapped.

### Revocation is a client-error verdict

eBay revokes the grant when a seller changes their password or login name. That
is not retryable, ever, and hammering it is how an integration gets rate-limited.
Such a connection is marked `reconnect_required`, its dead ciphertext is dropped,
and the card asks the merchant to reconnect.

The classification requires **both** an unrecoverable OAuth error code and a 4xx
status. A 5xx carrying an OAuth-shaped body is an eBay outage, and treating it as
consent withdrawn would make every merchant re-authorise because eBay had a bad
afternoon.

### `MULTI`/`EXEC`, not `GETDEL`

This one was found by running the code against the Redis this platform actually
deploys on, and it is worth recording because reasoning alone would have missed
it.

`GETDEL` is the obvious way to consume single-use state atomically. It was added
in **Redis 6.2**. The Windows deployments run the 3.0.504 build, where the
command does not exist — and the resulting `unknown command` error surfaces as
`EbayOAuthStateError`, which is *indistinguishable from a genuine CSRF
rejection*. Every eBay consent would have failed with "that authorization
request is no longer valid", and the logs would have said the state was invalid.

A `MULTI`/`EXEC` transaction containing `GET` then `DEL` gives exactly the same
guarantee — Redis runs a queued block with no other client's command interleaved,
so only one caller can see a non-nil value — on every server version from 1.2
onward, in one round trip.

**Operational note:** the local Redis is 3.0.504 while `docker-compose.yml` and
CI both run `redis:7-alpine`. That divergence is worth closing separately; until
it is, no new code should assume a command newer than Redis 3.0 without checking.

---

## Storage, and how it is erased

`ebay_connections` is the first table in this application to hold eBay personal
data. Under the EBAY-C0 contract, that obligated this milestone to declare it and
register an eraser **in the same change**:

| | |
|---|---|
| **Declared** | `EBAY_STORAGE_DECLARATIONS` in `app/integrations/ebay/deletion.py` |
| **Holds** | eBay immutable `userId`, display username, marketplace, account type, and encrypted access and refresh tokens |
| **Erased by** | `EbayConnectionOwner`, matched on `ebay_user_id` only |
| **How** | `DELETE`, physical — never `deleted_at` |

Three properties of the eraser, each with a reason:

* **A delete, not an anonymisation.** The row's whole purpose is to hold eBay
  credentials and eBay's account id. Anonymise the identifier and what remains is
  an encrypted access token belonging to an account we have been told to forget —
  worse than useless, because it is retained credential material with nothing left
  to associate it with.
* **Matched on `ebay_user_id` only.** eBay's notification also carries `username`
  and `eiasToken`. Matching on the username as a fallback would be worse than not
  matching at all: a seller who renamed may have freed their old name for someone
  else, and erasing the wrong workspace's connection is unrecoverable.
* **Deliberately crosses tenants.** eBay is not making a request on behalf of one
  workspace; it is saying a person is gone. The statement is one equality
  predicate on an indexed, globally-unique column — not a general query surface.

Tokens are encrypted at rest with the platform Fernet keys
(`SECURITY_ENCRYPTION_KEYS`). No response schema anywhere has a field capable of
holding a token, a ciphertext, or the immutable `ebayUserId` — that last one is
excluded on purpose, because putting it in an API response would create a second
place it has to be erased from.

---

## Deployment

### Server configuration

Set on the backend, never in a UI:

```
EBAY_CLIENT_ID=<App ID (Client ID) from the eBay portal>
EBAY_CLIENT_SECRET=<Cert ID (Client Secret)>
EBAY_REDIRECT_URI_NAME=Auto_Pilot          # the RuName, not a URL
EBAY_FRONTEND_RETURN_URL=https://app.whiteto.com/settings/integrations
CORS_ORIGINS=<must include https://app.whiteto.com>
SECURITY_ENCRYPTION_KEYS=<at least one Fernet key>
```

And the frontend must be **built** with:

```
NEXT_PUBLIC_API_URL=https://api.whiteto.com
```

`NEXT_PUBLIC_*` values are inlined at build time. A build made against a
loopback URL produces a bundle that calls the developer's own machine from every
visitor's browser, and no amount of runtime configuration fixes it afterwards.

`EBAY_REDIRECT_URI_NAME` is not a secret — it is an opaque public identifier
eBay resolves to the URLs above — but it is environment-specific, so it stays in
configuration and never in OAuth code.

`SECURITY_ENCRYPTION_KEYS` is a hard requirement, not hardening. `connect`
refuses to start a consent flow without it, deliberately: discovering the
platform cannot store a token *after* a seller has granted consent would waste
their time and leave a live credential with nowhere safe to go.

### Portal configuration — the manual step

In the eBay Developer Portal, under **User Tokens → Get a Token from eBay via
Your Application → Your eBay Redirect URL (RuName)**, register:

| Field | Value |
|---|---|
| Display title | `DropPilot AI` |
| RuName (production) | `Auto_Pilot` |
| Auth accepted URL | `https://api.whiteto.com/api/v1/integrations/ebay/callback` |
| Auth declined URL | `https://api.whiteto.com/api/v1/integrations/ebay/callback` |
| Privacy policy URL | `https://app.whiteto.com/privacy` |
| Frontend return URL | `https://app.whiteto.com/settings/integrations` |

**The privacy policy is served by this application** at `/privacy`, as a public
route excluded from the middleware's auth gate. It is therefore on the app host,
not the root domain — `whiteto.com` serves a different application and must not
be repurposed for it. eBay fetches this URL with no session, so a policy behind a
login would fail RuName validation.

Accepted and declined point at the same endpoint on purpose. The handler
distinguishes them by what eBay sends — `code` versus `error` — and a second
route would be the same code behind a different name, with two places to keep
correct.

The portal then displays the **RuName**, which is what goes in
`EBAY_REDIRECT_URI_NAME`. It is not the URL you just typed.

### Production frontend — a hard prerequisite, and it does not exist yet

**Discovered during the EBAY-C0.1 deployment on 27 August 2026, and it blocks any
C1 rollout.** The production worktree has never had a frontend installed:

```
C:\dsplive\frontend\.next             ABSENT
C:\dsplive\frontend\.next\standalone  ABSENT
C:\dsplive\frontend\node_modules      ABSENT
C:\dsplive\frontend\.env              ABSENT (only .env.example)
```

There is also no frontend Scheduled Task, no Startup entry and no registry `Run`
entry. What currently answers on port 3000 is a **`next dev` server running from
the primary checkout** `C:\Users\profe\Documents\DS Platform`, bound to `::`.
It is not deployable evidence of anything, it serves a different branch, and it
must not be mistaken for production.

C1's deliverable is a merchant-facing card. Until a real production frontend
exists, C1 cannot be validated in production no matter how green its tests are.

**Rollout sequence, in order:**

1. Deploy the exact accepted C1 SHA to `C:\dsplive`.
2. Decide and audit the production `NEXT_PUBLIC_API_URL`. It is **inlined at
   build time**, so it must be correct *before* the build, not after.
3. Create `C:\dsplive\frontend\.env` with restricted ACL, matching the backend
   `.env` ownership model. Never commit it.
4. `npm ci` in `C:\dsplive\frontend` — a clean, lockfile-exact install.
5. `npm run build`, then confirm `.next/standalone/server.js` exists.
   `next.config` sets `output: "standalone"`, so **`next start` is wrong** — use
   `npm run start:e2e` (`node scripts/start-standalone.mjs`). See TECHNICAL_DEBT
   M13 / audit A-06.
6. Start bound to **`127.0.0.1:3000` only**, no dev server, no hot reload,
   logs restricted and outside Git.
7. Cut over from the existing dev server deliberately: stop it only once the
   production build is verified and ready to bind, so port 3000 is never served
   by two processes at once.
8. Verify local HTTP 200, the public frontend URL, static assets, login render,
   and that the frontend reaches the production API.
9. Add a `DropPilot Frontend` Scheduled Task on the same logon-trigger model as
   `DropPilot Backend`, one instance, restart on failure. **A logon trigger is
   not a pre-login boot service** — the same limitation the backend task has.
10. Keep a rollback path to the previous UI service in case cutover fails.

None of this was performed in the C1 synchronization task, and none of it is
implied by C1's green test suite.

### Verifying after deployment

1. `GET /api/v1/integrations/ebay/status` as any authenticated user returns
   `configured: true`.
2. The integrations page shows an eBay card with an enabled **Connect eBay**
   button — not an "Unavailable" badge.
3. Clicking it lands on eBay's own consent screen at `auth.ebay.com`, showing
   the five audited permissions and no others.
4. Approving returns to `…/settings/integrations?ebay=connected`, and the card
   shows the seller's eBay username and marketplace.

---

## What EBAY-C1 does not do

Stated explicitly, because an unstated limitation is a trap for whoever hits it
next.

1. **No listing, inventory, pricing, order or fulfilment operation.** C1
   establishes an authenticated connection and nothing more. `access_token_for`
   is the single entry point every future eBay call will use, and today it has
   no callers outside `verify`.
2. **No background refresh.** Tokens are refreshed on demand, when something
   asks for one. A connection that is never used will sit with an expired access
   token until it is. That is harmless — the refresh token is valid for about
   18 months — but it means "connected" on the card is as fresh as the last call
   that used it, not a live probe. The index `ix_ebay_connections_status_expiry`
   exists so a future sweep has something to use.
3. **Disconnect does not revoke at eBay.** eBay's published OAuth documentation
   describes minting and refreshing tokens, and states that sellers revoke
   consent through their own eBay account pages; it does not document a
   revocation endpoint an application can call on a seller's behalf. Rather than
   invent a plausible-looking URL and report "revoked" on the strength of a
   guess, DropPilot destroys the local credentials — which is what actually stops
   this platform acting — and a merchant who wants the grant itself gone must
   remove DropPilot from their eBay account settings. If eBay documents such an
   endpoint later, this is a small addition to
   `EbayConnectionService.disconnect`.
4. **`verify` is not exposed as an endpoint.** It exists and is tested, but
   nothing calls it over HTTP yet; the card reads stored state. A "check
   connection" button is C2 work.
5. **No live eBay verification of the OAuth flow.** Every assertion is against
   eBay's published specifications and a mocked transport. No consent has been
   granted by a real seller, and no real eBay token has ever been exchanged.
   This is the same limitation EBAY-C0 carried until its endpoint was validated
   in production, and it is the honest state of this milestone.
6. **One eBay account per workspace.** A merchant operating several eBay accounts
   needs several DropPilot workspaces. Multi-account support is not planned for
   C2 and would change the uniqueness model.
7. **Sandbox is configurable but untested.** `EBAY_ENVIRONMENT=sandbox` selects
   the sandbox hosts and the state record refuses to complete across an
   environment change, but no sandbox flow has been run.

---

## The C1 / C2 boundary

C1 ends at "this workspace has a usable eBay access token, and the platform can
prove whose it is".

C2 begins at the first call that *uses* it: reading the seller's business
policies, marketplaces and inventory locations. Those are prerequisites for
listing, they need the `sell.account` scope C1 already requested, and they are
the first real exercise of `access_token_for` under load.

Nothing in C1 was built ahead for C2 — no policy models, no marketplace tables,
no listing scaffolding.

---

## Verification

| Gate | Result |
|---|---|
| Backend unit + integration | 96 eBay C1 tests pass; full suite green |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` (strict) | clean, 206 files |
| Migration `0030` upgrade → downgrade → re-upgrade | run against real PostgreSQL, passes |
| Frontend `lint` / `typecheck` / `build` | clean |
| Playwright (chromium + mobile-chrome) | eBay spec passes |
| Committed-secret scan | clean |

Assertions are against real PostgreSQL, real Redis, real Fernet encryption and
the real migrations. Only eBay's network is faked, and an unexpected outbound URL
raises rather than escaping to the network.
