# DropPilot AI

A multi-tenant SaaS platform for dropshipping automation.

> **Status: Phase 1 — foundation plus authentication.**
> There are no business features yet: no marketplace integrations, no product
> import, no orders, no billing. What exists is the architecture those features
> will be built on, plus a working multi-tenant authentication system. See
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
| [CLAUDE.md](CLAUDE.md) | Engineering constitution — read before writing code |
| [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md) | Phase status and deferred decisions |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each phase |
| [TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md) | Known debt, ranked, each with a trigger |
| [Architecture.md](docs/Architecture.md) | Layering, multi-tenancy, and the reasoning behind each major decision |
| [Authentication.md](docs/Authentication.md) | JWT flow, tenant resolution, token rotation, security decisions |
| [Database.md](docs/Database.md) | Schema, conventions, migrations |
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

Deliberate phase boundaries, not defects. Each is recorded so that none is
discovered late.

1. **Password reset is not implemented.** It needs email delivery, a signed
   single-use token, and its own expiry policy. The page exists and says so
   plainly rather than faking a confirmation email.

2. **Email verification is not implemented.** `is_verified` is set true on
   registration because there is no mail delivery to verify against.

3. **Registration discloses that an address is already taken.** Unavoidable
   without email delivery — the account is either creatable or not.

4. **One email across two tenants resolves to the earliest account.** See
   [Authentication.md](docs/Authentication.md#known-limitations) for the fix.

5. **No breached-password check, and no multi-factor authentication.** Both
   should land before live customer accounts exist.

6. **Docker images have not been built.** The Dockerfiles and Compose file are
   unverified — Docker is not installed on the development machine. The CI
   pipeline builds all three images, so the first pipeline run is the real test.

7. **Playwright tests have not been executed.** The specs are written and the
   config is in place, but no browser binaries were installed. Run
   `npx playwright install` then `npm run test:e2e`.

8. **Rate limiting fails open.** If Redis is unavailable the limiter allows all
   traffic rather than rejecting it — availability is preferred over
   enforcement. Redis therefore needs its own alerting, because while it is
   down there is no quota enforcement at all.

9. **Offset pagination.** Fine at current scale, degrades at deep offsets. Add
   a keyset variant alongside it before product catalogues reach the millions
   per tenant.

10. **Frontend API types are hand-written** and will drift from the server.
    Generate them from `/openapi.json` before the API surface grows.

## Licence

Proprietary. All rights reserved.
