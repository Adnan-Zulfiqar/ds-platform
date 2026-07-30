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

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app --reload
```

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

## Working with the API before authentication exists

Every `/api/v1` endpoint requires a tenant. There is no login yet, so the tenant
comes from an `X-Tenant-ID` header — which only works when `ENVIRONMENT=local`.

Create a tenant to work against:

```bash
docker compose exec postgres psql -U droppilot -d droppilot -c "INSERT INTO tenants (id, name, slug, status, is_active, timezone, default_currency, created_at, updated_at) VALUES (gen_random_uuid(), 'Acme', 'acme', 'trial', true, 'UTC', 'USD', now(), now()) RETURNING id;"
```

Then call the API with the returned id:

```bash
curl -H "X-Tenant-ID: <the-uuid>" http://localhost:8000/api/v1/users
```

Health endpoints need no tenant:

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
browser, not by the container. It must be `http://localhost:8000`, never
`http://backend:8000`.

**Changing a `NEXT_PUBLIC_*` value has no effect** — those are inlined at build
time. Rebuild: `docker compose up --build frontend`.

**`alembic upgrade head` fails on enum types** — a partially-applied migration
left the enums behind. `alembic downgrade base` then upgrade again, or recreate
the database with `docker compose down -v`.

**Rate limit errors locally** — set `SECURITY_RATE_LIMIT_ENABLED=false` in
`.env`.
