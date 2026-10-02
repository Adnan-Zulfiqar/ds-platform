# Development setup

Two supported paths. Docker is closer to production and needs no local Python or
Node. The native path gives faster feedback and working debugger breakpoints.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker + Compose | 24+ | Docker path |
| Python | 3.13+ | Native backend |
| Node.js | 22+ | Native frontend |
| Git | 2.40+ | Everything |

---

## Path A — Docker (recommended)

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

First build takes several minutes. Subsequent starts are seconds.

Apply the schema (required once, and after any migration):

```bash
docker compose exec backend alembic upgrade head
```

| Service | URL |
|---|---|
| Application via Nginx | http://localhost |
| Frontend direct | http://localhost:3000 |
| API direct | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| RabbitMQ management | http://localhost:15672 |

Useful commands:

```bash
docker compose logs -f backend
```

```bash
docker compose exec backend pytest
```

```bash
docker compose down -v
```

> `down -v` deletes the volumes, and therefore all local data.

---

## Path B — Native

### Data stores

The application needs PostgreSQL, Redis, and RabbitMQ. Run just those in Docker
and everything else natively:

```bash
docker compose up -d postgres redis rabbitmq
```

### Backend

```bash
cd backend && python -m venv .venv
```

Activate it — `.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
on macOS and Linux — then:

```bash
pip install -e ".[dev]"
```

```bash
cp ../.env.example .env
```

A run from source reads the root `.env` first and `backend/.env` second; a
value set in `backend/.env` wins. A blank line (`KEY=`) in either file counts
as "not set here", so the template's empty provider-key lines no longer erase
keys you filled in the root `.env`. To blank a value on purpose, set it as an
empty environment variable instead — that still overrides both files.

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app --reload --no-proxy-headers
```

`--no-proxy-headers` is required in every environment, not only production:
it keeps `app.core.client_ip` the single authority on whether a forwarding
header may be believed. Running without it lets anything on loopback choose
its own client address, which silently defeats every per-IP limit — so a
developer testing a throttle would get a misleading answer.

### Frontend

```bash
cd frontend && npm install
```

```bash
cp .env.example .env.local
```

```bash
npm run dev
```

---

## Verifying your setup

Backend — all four must pass:

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

End-to-end tests need browser binaries first:

```bash
cd frontend && npx playwright install
```

```bash
npm run test:e2e
```

---

## Working with the authenticated API

> The `X-Tenant-ID` header from Phase 0 **no longer exists.** Identity now comes
> from a signed access token. See [Authentication.md](Authentication.md).

Create an account. This provisions a tenant, its first user, and the owner role,
then signs you in:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" -d '{"companyName":"Acme Trading","email":"you@example.com","password":"Correct-Horse-Battery9"}'
```

The response contains `tokens.accessToken`. Use it as a bearer token:

```bash
curl http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access-token>"
```

Sign in again later:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"you@example.com","password":"Correct-Horse-Battery9"}'
```

Access tokens last 15 minutes. The refresh token is returned as an httpOnly
cookie, so with `curl` use a cookie jar:

```bash
curl -c jar.txt -b jar.txt -X POST http://localhost:8000/api/v1/auth/refresh -H "Content-Type: application/json" -d '{}'
```

Health endpoints need no authentication:

```bash
curl http://localhost:8000/health
```

---

## Common problems

**`connection refused` on port 5432** — Postgres has not finished starting.
`docker compose ps` shows health status; the API waits for it, but a native
backend does not.

**`SECURITY_SECRET_KEY is still the local placeholder`** — expected, and
correct. The application refuses to start in a deployed environment with the
default key. Set a real one, or keep `ENVIRONMENT=local`.

**Frontend cannot reach the API** — `NEXT_PUBLIC_API_URL` is resolved by the
browser, not by the container. Never use `http://backend:8000` (Compose DNS
is invisible to the browser).

- **Via nginx (`http://localhost`)** — bake `NEXT_PUBLIC_API_URL=http://localhost`
  so the SPA calls same-origin `/api` (proxied to the backend). This is the
  Compose default (audit A-05).
- **Direct backend (`npm run dev` or `:8000`)** — use
  `http://localhost:8000` (see `frontend/.env.example`).

**Changing a `NEXT_PUBLIC_*` value has no effect** — those are inlined at build
time. Rebuild: `docker compose up --build frontend`.

**`alembic upgrade head` fails on enum types** — a partially-applied migration
left the enums behind. `alembic downgrade base` then upgrade again, or recreate
the database with `docker compose down -v`.

**Rate limit errors running the full Playwright suite locally** — a serial
run of the full suite legitimately issues more than 100 real backend requests
per 60-second window from one IP (registration, dashboard fetches, ...),
which trips the same per-IP bucket a credential-stuffing attempt would. Raise
the ceiling rather than disabling the control: set
`SECURITY_RATE_LIMIT_REQUESTS=1000` in `.env` and restart the backend
(`--reload` does not pick up `.env` changes — only source changes). Leave
`SECURITY_RATE_LIMIT_ENABLED=true`, so the limiter stays exercised by the
suite. See [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) M13.
