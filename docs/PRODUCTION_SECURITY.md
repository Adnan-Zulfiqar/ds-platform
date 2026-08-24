# Production security

What must be true before DropPilot AI serves a real customer, and how to keep it
true afterwards.

Everything here is enforced by code or by CI. Where something is only a
convention, it says so — a document that cannot tell you which of its rules are
real is worse than no document.

---

## 1. Required environment variables

The application **refuses to start** when any of these is wrong in a deployed
environment (`ENVIRONMENT=production` or `staging`). Refusing is deliberately
harsher than warning: a warning in a container log is a warning nobody reads,
and every one of these failures is otherwise silent at runtime — the service
starts, reports healthy, and is wrong.

| Variable | Requirement | What happens if you get it wrong |
|---|---|---|
| `ENVIRONMENT` | `production` or `staging` | Left at `local`, **every guard below is disabled** |
| `SECURITY_SECRET_KEY` | ≥32 chars, not the placeholder | Startup refuses |
| `SECURITY_ENCRYPTION_KEYS` | ≥1 Fernet key, not published, not equal to the signing key | Startup refuses |
| `SECURITY_COOKIE_SECURE` | `true` | Startup refuses |
| `LOG_INCLUDE_REQUEST_BODY` | `false` | Startup refuses |
| `ALLOWED_HOSTS` | no wildcard | Startup refuses |
| `POSTGRES_PASSWORD` | set | **Compose** refuses |
| `RABBITMQ_PASSWORD` | set | **Compose** refuses |

`ENVIRONMENT` is the one to check twice. It is the switch that arms everything
else, and setting it wrong produces no error at all.

Start from [`.env.production.example`](../.env.production.example), which lists
every variable with blank secrets.

### Why the last two are enforced by Compose, not the application

The application cannot detect them. It receives a working DSN and has no way to
know the password was a default substituted by `${POSTGRES_PASSWORD:-droppilot}`.
The guard has to live where the substitution happens, which is why those two use
Compose's `:?` syntax instead.

---

## 2. Secret management

**Never commit a secret.** CI enforces this on every push — see
[`scripts/check_secrets.py`](../scripts/check_secrets.py), which fails the build
on a committed `.env`, a published Fernet key outside the test suite, a template
with a populated secret, or a re-introduced Compose default. Each of its five
rules is verified to actually fire; a check that cannot fail is not a check.

### The two keys, and why they are not one value

| | `SECURITY_SECRET_KEY` | `SECURITY_ENCRYPTION_KEYS` |
|---|---|---|
| Protects | JWT signatures | Stored supplier credentials |
| Rotating it | Immediate; ends every session | Requires re-encrypting stored data first |
| On suspected leak | Rotate **now** | Rotate over hours or days |

Sharing one value ties an urgent operation to a slow one: you could not rotate a
suspected-leaked signing key without first re-encrypting every stored
credential. Startup refuses the reuse for that reason.

### Two secrets that are public on purpose

Both are safe **only** because startup refuses them when deployed:

- `insecure-local-development-key-change-me` — the local signing key.
- The two Fernet keys in `backend/tests/conftest.py`. They decode to the ASCII
  `test-key-N-NEVER-USE-IN-PROD-!!!` so a reader can see what they are at a
  glance. Committed so that encryption and key rotation are *exercised* by the
  suite rather than assumed.

---

## 3. Rotation procedure

### Signing key — urgent, minutes

1. Generate: `openssl rand -base64 48`
2. Replace `SECURITY_SECRET_KEY` in the secret manager.
3. Restart the API and workers.

**Every session ends.** Access tokens fail signature verification and refresh
tokens are rejected, so every user signs in again. That is the intended cost of
an urgent rotation, and the reason this key must never double as the encryption
key.

### Encryption key — planned, hours to days

Never replace this key. **Prepend** to it:

1. Generate a new key:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set `SECURITY_ENCRYPTION_KEYS=<new>,<old>` — newest first.
3. Restart. New writes use the new key; existing ciphertext still decrypts with
   the old one.
4. Re-encrypt stored rows with `encryption.rotate()`.
5. Only once nothing references the old key, drop it.

There is no window in which stored data cannot be read, which is the whole point
of the list.

### Database and broker passwords

Rotate at the infrastructure layer, update the secret manager, restart. Nothing
in the application caches them.

---

## 4. Deployment checklist

Before the first real customer:

- [ ] `ENVIRONMENT=production` — **verify this first**; it arms everything else
- [ ] Every variable in §1 set from a secret manager, not a file on the host
- [ ] `SECURITY_SECRET_KEY` freshly generated, ≥32 chars, used nowhere else
- [ ] `SECURITY_ENCRYPTION_KEYS` freshly generated and **different** from the signing key
- [ ] `ALLOWED_HOSTS` lists real hostnames
- [ ] `CORS_ORIGINS` lists real origins, no wildcard
- [ ] TLS terminates in front of the application (`SECURITY_COOKIE_SECURE=true` assumes HTTPS)
- [ ] Postgres and Redis ports **not** published to the internet — `docker-compose.yml` publishes them for local convenience
- [ ] Source volume mounts removed; the compose file mounts `./backend` for live reload
- [ ] Migrations applied: `alembic upgrade head`
- [ ] `/health/ready` returns `ready: true` with database and redis healthy
- [ ] CI green, including the `secrets` job

Confirm the guards are live by attempting a bad start once, deliberately: set
`SECURITY_COOKIE_SECURE=false` and check the service refuses. A guard nobody has
seen fire is a guard nobody knows is wired up.

---

### Proxy boundary

- [ ] The running process shows `--no-proxy-headers` (`ps -o args= -C uvicorn`).
- [ ] `SECURITY_TRUSTED_PROXIES` is set to the address the proxy connects from
      — `127.0.0.1/32,::1/128` for the same-host Cloudflare Tunnel.
- [ ] The verification loop in §7 returns `429` once the quota is exhausted.

---

## 5. Local versus production

Local development deliberately runs with weaker settings. Each is safe only
because a deployed environment refuses it.

| Setting | Local | Production | Why local differs |
|---|---|---|---|
| `SECURITY_SECRET_KEY` | placeholder | generated | Zero-setup checkout |
| `SECURITY_ENCRYPTION_KEYS` | may be empty | required | Most work needs no credential storage |
| `SECURITY_COOKIE_SECURE` | `false` | `true` | Test client speaks HTTP; a Secure cookie would never be sent |
| `ALLOWED_HOSTS` | `*` | explicit | Hostname varies by developer |
| `LOG_JSON_OUTPUT` | `false` | `true` | A human reads local logs |
| API docs | on | off | `/docs` enumerates every endpoint for an attacker |

**Local JWTs are signed with a key published in this repository**, so any local
token is forgeable by anyone with the source. Fine for development; never treat
a local session as evidence of anything.

Tests that assert local development still starts with each of these weakened
settings live in `tests/unit/test_security_hardening.py`. Hardening that breaks
local development gets disabled, and then protects nothing.

---

## 6. What is enforced where

| Control | Enforced by | Verified by |
|---|---|---|
| Placeholder signing key rejected | `Settings.model_post_init` | `test_security_hardening.py` |
| Encryption keys present, private, distinct | `Settings._validate_deployed_encryption` | `test_security_hardening.py` |
| Secure cookie required | `Settings.model_post_init` | `test_security_hardening.py` |
| Wildcard host rejected | `Settings.model_post_init` | `test_config.py` |
| Request-body logging rejected | `Settings.model_post_init` | `test_config.py` |
| Secrets scrubbed from logs | `_redact_secrets` processor | `test_security_hardening.py` |
| No committed secrets | `scripts/check_secrets.py` | CI `secrets` job |
| No Compose secret defaults | `scripts/check_secrets.py` + `:?` | CI `secrets` job |
| Tenant isolation | `TenantScopedRepository._base_query` | per-repository isolation tests |
| Forwarding headers believed only from a configured proxy | `app/core/client_ip.py` | `test_client_ip_resolution.py`, `test_proxy_boundary_real_server.py` |
| Uvicorn cannot make a competing proxy-trust decision | `--no-proxy-headers` on every launch surface | `test_server_launch_surfaces.py` |

---

## 7. The proxy boundary

### One authority, not two

The application decides who a caller is in exactly one place:
`app/core/client_ip.py`, driven by `SECURITY_TRUSTED_PROXIES`. A forwarding
header is evidence only when the socket peer is inside a configured CIDR;
otherwise it is ignored entirely and the socket address is the identity.

That is worthless if the ASGI server has already made its own decision
underneath it — and by default uvicorn does. `proxy_headers` defaults to
**true** and `forwarded_allow_ips` to **`127.0.0.1`**, so uvicorn rewrites
`scope["client"]` from `X-Forwarded-For` for any request arriving on loopback,
before a single line of application middleware runs. The resolver then receives
a "socket peer" that the caller chose.

Loopback is not a hypothetical peer here: it is exactly where cloudflared
connects from.

**Measured against this repository's real launch command before the fix:** 700
requests rotating a forged `X-Forwarded-For` produced **zero** 429 responses
and 650 separate rate-limit buckets. The same 700 requests from one address
produced 53. The quota was not degraded — it was absent.

### The fix

Every launch path passes `--no-proxy-headers`. Uvicorn installs
`ProxyHeadersMiddleware` only when `proxy_headers` is true, so the flag removes
the rewrite rather than narrowing it.

`--forwarded-allow-ips` is **not** an acceptable substitute. It leaves uvicorn
parsing the chain with its own rules alongside the application's, and two
authorities that can disagree is worse than either alone. The launch-surface
guard rejects it for that reason.

### Canonical startup command

```bash
uvicorn app.main:app --no-proxy-headers --host 0.0.0.0 --port 8000
```

Local development adds `--reload`; nothing else differs, and the flag is
required in **both** modes. A developer testing a throttle without it gets a
misleading answer.

Hardened launch surfaces:

| Path | Environment |
|---|---|
| `run-backend.ps1` | native host — the Cloudflare Tunnel topology |
| `docker/backend.Dockerfile` | production image CMD |
| `docker-compose.yml` | compose stack (overrides the image CMD) |
| `.github/workflows/ci.yml` | CI end-to-end job |
| `docs/DevelopmentSetup.md` | documented developer command |

`backend/tests/unit/test_server_launch_surfaces.py` holds this list and fails
if any entry loses the flag, reintroduces uvicorn-side trust, **or if a new
uvicorn invocation appears anywhere in the repository that the list does not
cover.** Hardening five commands is worth nothing if a sixth is added later and
nobody notices.

### Cloudflare Tunnel deployment contract

```
internet client -> Cloudflare edge -> cloudflared (same host)
                -> uvicorn over loopback -> app.core.client_ip
```

The deployment environment must set:

```
SECURITY_TRUSTED_PROXIES=127.0.0.1/32,::1/128
```

Set it to the address the connector arrives **from**, never the address it
listens on. Behind Nginx in a Compose network, use that network's CIDR. The
committed default stays blank, and blank is safe: every forwarding header is
ignored and per-IP limits become per-deployment limits — visibly wrong rather
than silently forgeable. No private deployment address is committed anywhere in
this repository.

With the contract in place: uvicorn hands over the true peer (`127.0.0.1`), the
application sees that it is a configured trusted proxy, and only then does it
read `CF-Connecting-IP` / `X-Forwarded-For`. The chain is walked right-to-left,
so a prefix the original caller added does not win. `CF-Ray` grants nothing —
anyone can send one. A malformed value stops the walk and falls back to the
peer, and every identity is a parsed address rendered back to text, so nothing
attacker-shaped can reach a Redis key.

### Bind address — audited, deliberately unchanged

Both `run-backend.ps1` and the container CMD bind `0.0.0.0`.

With proxy parsing disabled the bind address is no longer a spoofing vector: a
caller from any peer outside `SECURITY_TRUSTED_PROXIES` has its headers ignored
regardless of which interface it reached. Binding loopback would not close the
remaining local-process case either, since that case *is* loopback.

Consumers checked before leaving it alone: the frontend calls
`http://127.0.0.1:8000` (loopback); the Celery worker makes no HTTP call to the
API; the container health check uses `localhost` inside the container; Nginx
reaches the backend by Compose service name and **requires** `0.0.0.0`;
cloudflared connects over loopback. Narrowing the native path to `127.0.0.1`
would work for all of these but would break LAN and mobile testing against a
development server, for no security gain. Left as is, recorded here so the next
reader does not have to re-derive it.

### Verifying a deployment

With the API running, from the API host — 40 requests, each with a different
forged forwarding header:

```bash
for i in $(seq 1 40); do curl -s -o /dev/null -w "%{http_code}\n" -H "X-Forwarded-For: 203.0.113.$i" http://127.0.0.1:8000/api/v1/proxy-boundary-check; done | sort | uniq -c
```

Expected: a run of `429` once the general quota is exhausted. If every response
is `404` and none is `429`, forged headers are still minting fresh quotas and
the server is running without `--no-proxy-headers`.

Confirm the flag is on the running process:

```bash
ps -o args= -C uvicorn
```

### Rollback

Remove `--no-proxy-headers` from the launch command and restart. Nothing else
changes: no migration, no schema change, no API contract change, and
`SECURITY_TRUSTED_PROXIES` may stay set. Doing so restores the bypass described
above, so treat it as an incident action rather than a configuration option —
`test_server_launch_surfaces.py` fails until the flag is restored.

### eBay activation gate

> **EBAY-C0 must not be activated in production until the deployment runs the
> hardened launch configuration.** The eBay compliance endpoint is
> unauthenticated and public, and its 600/minute budget is per-client. Under a
> server that still parses proxy headers that budget is per-forged-header —
> which is to say, none. Activation means entering the endpoint and
> verification token in the eBay Developer Portal; do not do it until
> `--no-proxy-headers` and `SECURITY_TRUSTED_PROXIES` are both confirmed on the
> running process.

---

## 8. Known gaps

Stated plainly rather than omitted.

- **The deployment path has never been executed on this machine.** Docker and
  WSL are both absent, so images have never been built and the stack has never
  run locally. CI builds the images and validates the Compose file; a full
  running stack with passing health checks is **best-effort in CI and not
  verified here**. See C1 in `TECHNICAL_DEBT.md`.
- **No secret manager is wired up.** The documented path is environment
  variables; nothing integrates with Vault, AWS Secrets Manager or similar.
- **No automated key rotation.** §3 is a manual runbook.
- **Webhook signature verification is opt-in**, not mandatory (M11).
- **Email verification is implemented but disabled** — no mail provider (H4).
- **`SECURITY_TRUSTED_PROXIES` is still a manual deployment step.** Nothing can
  infer it, and nothing fails at startup when it is missing behind a proxy —
  the symptom is per-deployment rather than per-client limits. §7 is the
  contract; there is no automated check that the deployed value matches the
  real topology.
- **A local process on the API host can still choose its own client address**
  once loopback is a configured trusted proxy, which the Cloudflare Tunnel
  contract requires. That is inherent to trusting loopback rather than a defect
  in the resolver: anything already running on that host is inside the trust
  boundary.
- **Nginx's `X-Forwarded-For` comment is inaccurate.** It says the header is
  overwritten; `$proxy_add_x_forwarded_for` appends. The behaviour is safe
  because the resolver walks right-to-left, but the comment misleads. Left
  unchanged here to keep this change confined to the uvicorn boundary.
