# Production deployment guide

How to run DropPilot AI, in development and in production, and what differs
between them.

> **Read this first.** The full stack has **never been executed on the
> development machine** — Docker Desktop is installed but its Linux engine
> cannot start without WSL2, which needs administrator elevation and a reboot.
> Everything below that concerns containers is verified **in CI only**. See
> [PRODUCTION_READINESS_AUDIT.md](PRODUCTION_READINESS_AUDIT.md) for exactly
> what that covers and what it does not.

Security configuration is in [PRODUCTION_SECURITY.md](PRODUCTION_SECURITY.md).
This guide covers how to bring the pieces up; that one covers what must be true
about the secrets they use.

---

## Part 1 — Development

The stack is designed to run natively so that work is possible without Docker.
That is how it has actually been developed.

### Native (the path this project uses)

```bash
# One-time
cp .env.example .env                 # supplies working local defaults
cd backend && python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"

# Services: PostgreSQL 17 and Redis must be running locally
cd backend && .venv/Scripts/python -m alembic upgrade head

# Run
./run-backend.ps1                    # http://localhost:8000
cd frontend && npm run dev           # http://localhost:3000
```

`cp .env.example .env` is not optional. Since M18, Compose refuses to start
without `POSTGRES_PASSWORD` and `RABBITMQ_PASSWORD`, and the file is where they
come from locally.

### Compose

```bash
cp .env.example .env
docker compose up --build
```

Brings up Postgres, Redis, RabbitMQ, backend, worker, beat, frontend and nginx.

**This compose file targets development.** It publishes database ports, mounts
source for live reload, and enables `--reload`. Do not deploy it unchanged —
see Part 2.

### Celery

```bash
cd backend
.venv/Scripts/celery -A app.workers.celery_app.celery_app worker --loglevel=info
.venv/Scripts/celery -A app.workers.celery_app.celery_app beat   --loglevel=info
```

Needs a broker. RabbitMQ is not installed on the development machine, so worker
execution is verified in CI rather than locally (M15).

---

## Part 2 — Production

### Environment

Start from [`.env.production.example`](../.env.production.example). Every secret
there is blank and must come from a secret manager, not a file on the host.

The application **refuses to start** if `SECURITY_SECRET_KEY` is the placeholder
or under 32 characters, `SECURITY_ENCRYPTION_KEYS` is empty or publicly known or
equal to the signing key, `SECURITY_COOKIE_SECURE` is false, `ALLOWED_HOSTS`
contains a wildcard, or `LOG_INCLUDE_REQUEST_BODY` is true.

`ENVIRONMENT=production` is what arms all of that. Set it wrong and every check
above silently switches off — it is the single most consequential line in the
file.

### Database

PostgreSQL 17. Managed (RDS, Cloud SQL, Neon) is preferred: backups, failover
and patching are not things worth rebuilding.

```bash
alembic upgrade head
```

Run migrations as a **separate step before** starting the application, not from
an entrypoint. Two API replicas starting at once would otherwise race to migrate
the same database.

Connection budget: each API process holds its own pool, so peak connections are
`(POSTGRES_POOL_SIZE + POSTGRES_MAX_OVERFLOW) x replicas`. With defaults that is
30 per replica — check it against the server's `max_connections` or put PgBouncer
in front.

Never expose 5432 publicly. The development compose file publishes it for
convenience; production must not.

### Redis

Redis 7. Used for OAuth state, rate limiting and caching.

**OAuth state is the part that matters.** It is the CSRF defence for the supplier
redirect, and the flow fails closed without it — a connection cannot be started
if the state cannot be stored. Redis being down therefore blocks new supplier
connections, though it does not break existing ones: the rate limiter fails
*open* by design, so a Redis outage degrades to unthrottled rather than to an
outage.

Set `REDIS_PASSWORD` and do not expose 6379.

Note the local development machine runs Redis 3.0.504, which predates RESP3, so
the client is pinned to `protocol=2` (M14). That pin is harmless against Redis 7
but means local behaviour is not evidence about production.

### RabbitMQ

RabbitMQ 4. Broker for Celery.

`RABBITMQ_PASSWORD` is mandatory — Compose refuses to start without it. Do not
expose 5672 or the management UI on 15672.

### Celery

```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=info --concurrency=4
celery -A app.workers.celery_app.celery_app beat   --loglevel=info
```

**Exactly one beat process.** Two would each fire the same schedule, so every
periodic task would run twice. Workers scale horizontally; beat does not.

`task_acks_late` is enabled, giving at-least-once delivery — **every task must be
idempotent**, and the existing ones are (product import upserts on
`(tenant_id, source, external_id)`).

### Frontend

```bash
docker build -f docker/frontend.Dockerfile \
  --build-arg NEXT_PUBLIC_API_URL=https://api.droppilot.ai .
```

`NEXT_PUBLIC_*` values are **inlined into the client bundle at build time**, so
they are baked into the image and visible to anyone who loads the page. Never
put a secret in one, and rebuild the image to change the API URL.

### Nginx

`nginx/conf.d/default.conf` proxies `/api` to the backend and everything else to
the frontend. In production it also needs:

- TLS termination with a real certificate
- HTTP redirected to HTTPS
- `proxy_set_header X-Forwarded-Proto https`, so the application knows the
  original scheme

`SECURITY_COOKIE_SECURE=true` assumes TLS terminates in front. Without it the
browser will not send the refresh cookie and every session silently ends at the
first refresh.

### Order of operations

1. Provision Postgres, Redis, RabbitMQ
2. Load secrets into the secret manager
3. `alembic upgrade head`
4. Start the backend; confirm `/health/ready` returns `ready: true`
5. Start worker, then beat
6. Build and deploy the frontend with the correct `NEXT_PUBLIC_API_URL`
7. Point nginx at both; confirm TLS

Work through the checklist in
[PRODUCTION_SECURITY.md §4](PRODUCTION_SECURITY.md#4-deployment-checklist)
before the first real customer.

---

## Part 3 — What differs

| | Development | Production |
|---|---|---|
| Secrets | `.env` with published defaults | Secret manager, all unique |
| `ENVIRONMENT` | `local` | `production` — arms every guard |
| Database port | published (5432) | closed |
| Source | mounted for live reload | baked into the image |
| Reload | `--reload` | off |
| API docs | `/docs` on | off |
| Cookies | `Secure=false` (HTTP test client) | `Secure=true` |
| Logs | colour console | JSON |
| Migrations | run by hand | separate step before rollout |

Each development weakening is safe **only** because a deployed environment
refuses it. Tests assert the local side still works, because hardening that
breaks local development gets disabled and then protects nothing.

---

## Troubleshooting

**Application will not start in production.** Read the error — every startup
refusal names the variable. It is doing what it was built to do.

**Sessions end immediately after sign-in.** `SECURITY_COOKIE_SECURE=true`
without TLS in front: the browser will not send the cookie back.

**Supplier connections fail with "not connected".** Redis is unreachable. OAuth
state fails closed on purpose.

**Every periodic task runs twice.** Two beat processes.

**Frontend calls the wrong API.** `NEXT_PUBLIC_API_URL` is baked in at build
time. Rebuild the image.
