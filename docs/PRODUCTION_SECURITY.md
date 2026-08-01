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

---

## 7. Known gaps

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
