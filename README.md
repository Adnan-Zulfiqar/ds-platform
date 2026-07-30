# DropPilot AI

A multi-tenant SaaS platform for dropshipping automation.

> **Status: Phase 0 — foundation only.**
> There are no business features. No marketplace integrations, no product
> import, no orders, no billing, and **no authentication**. What exists is the
> architecture those features will be built on. See
> [Known limitations](#known-limitations) before deploying anything.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS, shadcn-style UI, React Query, Zustand |
| Backend | FastAPI, Python 3.13, SQLAlchemy 2 (async), Alembic |
| Data | PostgreSQL 17, Redis 7 |
| Async | Celery, RabbitMQ 4 |
| Infrastructure | Docker, Docker Compose, Nginx, GitHub Actions |
| Testing | Pytest, Playwright |

## Quick start

Requires Docker and Docker Compose.

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

Then apply the database schema:

```bash
docker compose exec backend alembic upgrade head
```

| Service | URL |
|---|---|
| Application (via Nginx) | http://localhost |
| Frontend (direct) | http://localhost:3000 |
| API (direct) | http://localhost:8000 |
| API documentation | http://localhost:8000/docs |
| RabbitMQ management | http://localhost:15672 |

For running the services without Docker, see
[docs/DevelopmentSetup.md](docs/DevelopmentSetup.md).

## Documentation

| Document | Contents |
|---|---|
| [Architecture.md](docs/Architecture.md) | Layering, multi-tenancy, and the reasoning behind each major decision |
| [FolderStructure.md](docs/FolderStructure.md) | Where code belongs and why |
| [CodingStandards.md](docs/CodingStandards.md) | Conventions and enforced rules |
| [DevelopmentSetup.md](docs/DevelopmentSetup.md) | Local setup, with and without Docker |
| [Contributing.md](docs/Contributing.md) | Branching, commits, review expectations |

## Verification

Every check below passes as of the end of Phase 0.

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

## Known limitations

These are deliberate Phase 0 boundaries, not defects. Each is recorded so that
none of them is discovered late.

1. **No authentication.** Tenant identity comes from an `X-Tenant-ID` header,
   which is client-controlled and therefore *not* access control. The resolver
   refuses to run in a deployed environment, so shipping this as-is fails
   loudly rather than leaking data. See `backend/app/api/deps.py`.

2. **The initial migration has not been run against a live PostgreSQL.** It was
   written by hand and is unverified. Run `alembic upgrade head` against a
   throwaway database and confirm the schema before relying on it.

3. **Docker images have not been built.** The Dockerfiles and Compose file are
   unverified — Docker was not available on the machine where Phase 0 was
   written. The CI pipeline builds all three images, so the first pipeline run
   is the real test.

4. **Playwright tests have not been executed.** The specs are written and the
   config is in place, but no browser binaries were installed. Run
   `npx playwright install` then `npm run test:e2e`.

5. **Rate limiting fails open.** If Redis is unavailable the limiter allows all
   traffic rather than rejecting it — availability is preferred over
   enforcement. Redis therefore needs its own alerting, because while it is
   down there is no quota enforcement at all.

6. **Offset pagination.** Fine at current scale, degrades at deep offsets. Add
   a keyset variant alongside it before product catalogues reach the millions
   per tenant.

## Licence

Proprietary. All rights reserved.
