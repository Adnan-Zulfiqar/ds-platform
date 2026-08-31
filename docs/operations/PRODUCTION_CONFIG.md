# Production configuration — matrix and runbook

What has to be true before this application may run as a deployed environment,
how to check it without deploying, and the order to change things in.

**No value appears in this document, and none appears in the tool's output.**
Both name settings and describe properties.

---

## 1. Why this exists

Every production guard in the application is conditional on
`Environment.is_deployed`. The live host is configured as `ENVIRONMENT=local`,
so **none of them is in force** — the process starts, reports healthy, serves
1,268 tenants, and every check that was written to protect it is switched off.

The tempting fix is to set `ENVIRONMENT=production` and restart. That would fail
immediately, and it would fail on the *first* guard only, leaving the operator to
discover the rest one restart at a time. Worse, the guards exist precisely
because the configuration behind them is not ready: changing the label without
changing the configuration would turn a silent problem into an outage.

So the order is: **make every deployed guard passable, prove it, and only then
change the label.**

## 2. Checking a configuration

```bash
cd backend
python scripts/verify_production_config.py \
    --env-file /path/to/.env \
    --expect-environment production \
    --expect-database droppilot
```

Nothing is started: no database connection, no Redis connection, no HTTP
request, no write. The file is parsed, settings are built from it in that
process, and the shared rule table is evaluated.

Both expectations are mandatory arguments on purpose. A configuration that is
internally perfect but points at the wrong database is not a good
configuration, and a tool that cannot tell you so is not much of a check.

### Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Ready | Every rule passes and nothing blocks activation |
| 1 | Usage or IO error | The invocation or the file was wrong. **Not a verdict about the environment** |
| 2 | Configuration invalid | A deployed environment must not start with this. Fix the FAIL rows |
| 3 | Publication blocked | The configuration is fit; something outside it forbids going live. **Do not resolve by editing configuration** |
| 4 | Operational dependency missing | An enabled integration is missing something it needs |

### Statuses

| Status | Meaning |
|---|---|
| `PASS` | The rule is satisfied |
| `FAIL` | Wrong, and a deployed environment must not start |
| `BLOCKED` | Correct, and something outside the configuration forbids going live |
| `MISSING` | An enabled integration is missing a dependency |
| `SKIPPED` | The integration is switched off. **Not a defect** |

`SKIPPED` matters as much as `FAIL`. An absent optional integration reported as
a failure teaches an operator to skim the report, which is how a real failure
gets missed.

### The shell cannot interfere

Every application variable is removed from the process before the file is
loaded, so the file is the only source. Anything removed is **reported by name**
rather than dropped quietly: an operator whose shell had exported
`SECURITY_SECRET_KEY` would otherwise read a clean report about a configuration
that does not exist on disk.

Removed variables are reported in two categories, both derived from a single
snapshot of the environment:

| Heading | Meaning | Marker |
|---|---|---|
| `AMBIENT OVERRIDES REMOVED` | Set in the shell **and** present in the file. The file wins; the shell copy would have shadowed it | `!` |
| `AMBIENT ONLY` | Set in the shell, **absent** from the file. Removed and ignored, so the report describes the file alone | `~` |

Names only, sorted, so two runs of the same file produce byte-identical output
and can be diffed. **Values are never shown** — knowing *which* setting the
shell was shadowing is what an operator needs; knowing what it held is what an
attacker needs. Variables the application does not read are neither removed nor
reported.

The environment is restored when the run ends, on every path including errors,
so running the checker twice in one process is safe and the second run is not
auditing the first one's leftovers.

> The first version of this tool took the snapshot **twice** — once to find the
> overridden variables and once to find the ambient-only ones. The second call
> had nothing left to remove, so `AMBIENT ONLY` was silently always empty. The
> shape changed rather than the call count: one call now returns both
> categories, so there is no second call to get wrong.

### Byte-order marks

A file that begins with a UTF-8 BOM is read correctly. Windows editors add one
routinely, and without handling it the first key would parse as a name beginning
with U+FEFF — so a file whose very first line is `ENVIRONMENT=production` would
read as declaring no environment at all, and the operator would be shown a
problem invisible in their editor.

Only a mark at the **start of the file** is removed; the file is read as
`utf-8-sig`, which is otherwise identical to `utf-8`. A U+FEFF inside a value is
left alone, because there it is data. Malformed lines are still reported by line
number only, BOM or no BOM.

## 3. The matrix

`Now` = required before the environment may be marked deployed.
`Activation` = required before the feature it governs is switched on.

| Setting | Secret | When | Owner | Rule | Production today |
|---|---|---|---|---|---|
| `ENVIRONMENT` | no | Now | operator | a deployed value (`production`/`staging`) | **unsafe** — `local`, so no guard applies |
| `POSTGRES_DB` | no | Now | operator | must be the intended database | present, correct |
| `POSTGRES_HOST` / `PORT` / `USER` | no | Now | operator | reachable instance | present |
| `POSTGRES_PASSWORD` | **yes** | Now | secret store | non-empty | present |
| `REDIS_HOST` / `PORT` | no | Now | operator | reachable instance | present |
| `REDIS_CACHE_DB` / `SESSION_DB` / `RATE_LIMIT_DB` | no | Now | operator | three distinct indices | present |
| `REDIS_PASSWORD` | **yes** | Activation | secret store | set if Redis is reachable off-host | absent — acceptable only while Redis is loopback-only |
| `SECURITY_SECRET_KEY` | **yes** | Now | secret store | unique, ≥32 chars, not the published placeholder | **unsafe** — the published placeholder |
| `SECURITY_ENCRYPTION_KEYS` | **yes** | Now | secret store | valid Fernet keys, none published, not the signing key | present, valid |
| `SECURITY_OTP_HMAC_KEY` | **yes** | Now | secret store | non-default, distinct from the signing key | **unsafe** — absent, so the published default applies |
| `SECURITY_COOKIE_SECURE` | no | Now | operator | `true` | **unsafe** — `false` |
| `SECURITY_TRUSTED_PROXIES` | no | Now | operator | the CIDRs of the real edge | present |
| `ALLOWED_HOSTS` | no | Now | operator | explicit hostnames, never `*` | **unsafe** — `*` |
| `CORS_ORIGINS` | no | Now | operator | the public https frontend origin | **unsafe** — loopback |
| `LOG_INCLUDE_REQUEST_BODY` | no | Now | operator | `false` | present, correct |
| `LOG_JSON_OUTPUT` | no | Activation | operator | `true` where logs are shipped | present |
| `NEXT_PUBLIC_API_URL` | no | Activation | frontend build | public https origin, **inlined at build time** | **unsafe** — loopback |
| `SHOPIFY_FRONTEND_RETURN_URL` | no | Activation | operator | public https URL | **unsafe** — loopback |
| `ALIEXPRESS_FRONTEND_RETURN_URL` | no | Activation | operator | public https URL | **unsafe** — loopback |
| `EBAY_FRONTEND_RETURN_URL` | no | Activation | operator | public https URL | absent |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | **yes** | Activation | eBay developer portal | set together | present |
| `EBAY_REDIRECT_URI_NAME` | no | Activation | eBay developer portal | the RuName | present |
| `EBAY_MARKETPLACE_DELETION_ENDPOINT` | no | Activation | operator | public https endpoint eBay can reach | present, https |
| `GOOGLE_OAUTH_CLIENT_ID` | no | Activation | Google Cloud console | set only when Google sign-in is activated | absent — correct while disabled |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | no | Activation | frontend build | must equal the backend value | absent |
| `EMAIL_PROVIDER` | no | Activation | operator | `resend` only when a key exists | absent → `stub`, correct while disabled |
| `RESEND_API_KEY` | **yes** | Activation | Resend dashboard | required when the provider is `resend` | absent |
| `EMAIL_FROM` | no | Activation | operator | must use the verified `auth.whiteto.com` domain | absent |
| `TERMS_PUBLISHED` | no | Activation | legal | `true` only after solicitor approval | **blocked** — false by design |
| Backups | n/a | Activation | operator | a regime with tested restoration | **blocked** — none exist |

## 4. Runbook: making production deployable

Nothing here may be done by editing `ENVIRONMENT` first.

### Step 1 — Rotate the signing key *(required, disruptive)*

`SECURITY_SECRET_KEY` is currently the placeholder published in this repository.
Anyone who can read the source can mint an access token for any account.

Generate a new one into the secret store, never into the repository:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Rotating it invalidates every existing session and refresh token.** Every
signed-in user is signed out. Do it at a quiet moment and tell people first.
There is no way to rotate this gradually — the key is the only thing that
verifies a token.

*Rollback:* restoring the previous value restores the old sessions, but the old
value is public and must not be restored except to buy time.

### Step 2 — Set a distinct OTP key *(required)*

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

It must differ from the signing key, so one leak does not compromise both.
Rotating it invalidates outstanding password-reset codes and tickets, which
expire in ten minutes anyway — harmless, but it strands anyone mid-flow.

*Rollback:* safe. Nothing durable depends on it.

### Step 3 — Secure the refresh cookie *(required)*

`SECURITY_COOKIE_SECURE=true`. The cookie is the longest-lived credential a
browser holds; without `Secure` it travels over plain HTTP.

*Rollback:* trivially reversible, but reversing it reopens the exposure. If
sign-in breaks after this change, the cause is that the site is being served
over HTTP — fix the TLS termination, not the flag.

### Step 4 — Name the real hostnames *(required)*

`ALLOWED_HOSTS` must list the real public hostnames rather than `*`. A wildcard
permits Host header attacks, including poisoned password-reset links.

*Rollback:* if a legitimate host is missing, requests to it return a Host
rejection. Add the host; do not restore the wildcard.

### Step 5 — Point the browser-facing URLs at the public origin *(required)*

`CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`, and the three OAuth return URLs must be
public `https://` URLs. Today they are loopback, which means:

* the browser application cannot call the API from anywhere but this machine;
* a merchant completing a Shopify or AliExpress OAuth flow is returned to an
  address that does not exist for them.

`NEXT_PUBLIC_*` is **inlined into the frontend bundle at build time**. Setting it
on the server afterwards does nothing — the frontend must be rebuilt.

### Step 6 — Re-run the checker

```bash
python scripts/verify_production_config.py --env-file <file> \
    --expect-environment production --expect-database droppilot
```

Expect exit **3** (publication blocked), not 0: the Terms are unpublished and
there are no backups. That is the correct state until those are resolved, and it
is the proof that every *configuration* rule now passes.

### Step 7 — Only now, change the label

Set `ENVIRONMENT=production` and restart the backend. The startup guards read
the same rule table the checker does, so a clean run at step 6 means the process
will start.

*Rollback:* setting it back to `local` restarts successfully but switches every
guard off again. It is a rollback of the protection, not of a change — treat it
as an incident, not a routine step.

## 5. What the checker cannot tell you

Stated plainly, because a green report should not be read as more than it is.

* **Whether the database, Redis or the network actually work.** It never
  connects to anything.
* **Whether a secret is *good*.** It checks a secret is not a published default,
  not that it was generated well or stored safely.
* **Whether the frontend bundle was built with the values in the file.**
  `NEXT_PUBLIC_*` is inlined at build time; the file is only evidence of intent.
* **Whether backups work.** There are none, so there is nothing to test.
* **Whether the Terms are lawful.** That is a solicitor's judgement.
* **Whether the file it read is the one production uses.** It audits the path it
  is given. Point it at the wrong file and it will tell you, accurately, about
  the wrong file.

## 6. Why the checker and the application cannot disagree

The rules live once, in `app/core/production_readiness.py`, as data.
`Settings.model_post_init` walks the entries marked as enforced at startup and
refuses to boot on the first failure; the CLI walks all of them and reports
everything at once.

**The asymmetry is deliberate.** A process that must not start does not benefit
from a full report — it needs to stop, and stopping on the first failure is the
existing behaviour every current test asserts. An operator preparing a
deployment needs the opposite: the whole picture, so they change one file once
instead of discovering the next problem on the next restart. Same rules, two
readings, chosen for who is looking.

That is also why a configuration the CLI reports as invalid may still boot
today: the CLI applies rules the startup path deliberately does not enforce —
`CORS_ORIGINS`, the browser-facing URLs — because adding a new boot-time refusal
to a running service is a change of behaviour, not a check. Those appear as
`FAIL` in the report and are listed in the matrix as required-now. `tests/unit/test_prod_h1_readiness.py` walks the same table
and, for each startup-enforced rule, breaks that one setting and asserts both
that the evaluator reports `FAIL` **and** that constructing `Settings` raises.

A rule added later without a matching breakage case fails that test, so the
equivalence cannot quietly lapse.
