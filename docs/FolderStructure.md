# Folder structure

Where code belongs, and why. When adding a file, find its layer here first — a
file in the wrong layer is how architectural boundaries erode.

## Repository root

```
/
├── backend/           FastAPI application
├── frontend/          Next.js application
├── docker/            Dockerfiles (one per runnable image)
├── nginx/             Reverse proxy configuration
├── docs/              This documentation
├── scripts/           Operational scripts
├── docker-compose.yml Local development stack
├── .env.example       Backend and Compose configuration template
└── .github/workflows/ CI pipelines
```

## Backend

```
backend/
├── alembic/                    Migration environment
│   └── versions/               Migration scripts (timestamped, slugged)
├── app/
│   ├── api/                    PRESENTATION — HTTP only
│   │   ├── deps.py             Composition root: all DI wiring
│   │   ├── error_handlers.py   Exception → HTTP response, in one place
│   │   └── v1/                 Version 1
│   │       ├── router.py       Aggregate router (a manifest, nothing more)
│   │       ├── health.py       Liveness / readiness / detailed health
│   │       ├── auth/           Reserved
│   │       ├── users/          Implemented — reference for the request pipeline
│   │       ├── products/       Reserved
│   │       ├── stores/         Reserved
│   │       ├── orders/         Reserved
│   │       └── analytics/      Reserved
│   ├── services/               BUSINESS LOGIC — no SQL, no HTTP
│   ├── repositories/           DATA ACCESS — the only place SQL is built
│   │   └── base.py             Tenant isolation is enforced here
│   ├── models/                 ORM mappings
│   │   └── base.py             Declarative base and shared mixins
│   ├── schemas/                Pydantic request/response contracts
│   ├── core/                   Cross-cutting; depends on nothing above it
│   │   ├── config.py           All configuration enters here
│   │   ├── context.py          Request-scoped tenant / user / request id
│   │   ├── exceptions.py       Domain exception hierarchy
│   │   ├── logging.py          Structured logging
│   │   └── redis.py            Redis clients and cache helper
│   ├── middleware/             ASGI middleware
│   ├── tasks/                  ENTRY POINT — background work (empty in Phase 0)
│   ├── workers/                INFRASTRUCTURE — Celery app, base task, retries
│   ├── events/                 Domain events (reserved)
│   ├── utils/                  Pure functions, no dependencies on other layers
│   ├── database/               Engine, session factory, health check
│   └── main.py                 Application factory
├── tests/
│   ├── conftest.py             Shared fixtures
│   ├── unit/                   No external dependencies; run on every commit
│   └── integration/            Require Postgres or Redis
└── logs/                       Local log output (gitignored)
```

### Entry points

Two packages are entry points into the domain, and they are deliberately
siblings:

- **`api/`** — HTTP requests
- **`tasks/`** — queued messages

Both are adapters that translate an external trigger into a service call.
Neither is imported by anything beneath it. `workers/` is not an entry point; it
is the infrastructure that runs tasks, in the same way that `main.py` is the
infrastructure that runs routes.

### Deviations from the originally specified layout

Two remain, each recorded here because folder structure is not changed silently.

| Specified | Implemented | Reason |
|---|---|---|
| `config/` at top level | `core/config.py` | Configuration is imported by 12 modules across every layer, which is what `core` is for. If it outgrows one file it becomes `core/config/` as a package with **no import changes**, since `app.core.config` resolves to either form. |
| `exceptions/` at top level | `core/exceptions.py` | `core/redis.py` imports `CacheError`. A top-level package would make `core` depend on a non-core package, turning the invariant "core depends on nothing outside itself" into a special case. |

Note that the exceptions rationale is narrower than it may appear:
`exceptions.py` imports nothing from the application, so no circular import is
possible either way. The benefit is a cleanly checkable layering invariant, not
a technical constraint.

A third deviation — `tasks/` nested inside `workers/` — was **corrected** during
Phase 0 finalization. The original justification (tasks are meaningless without
the Celery app) proved too much: API routers are equally meaningless without the
FastAPI app, yet `api/` is correctly top-level. See the entry-points note above.

## Frontend

```
frontend/
├── app/                        App Router
│   ├── layout.tsx              Root layout: fonts, providers, skip link
│   ├── page.tsx                / → redirect
│   ├── error.tsx               Route-level error boundary
│   ├── loading.tsx             Streaming fallback
│   ├── not-found.tsx           404
│   ├── globals.css             Design tokens for both themes
│   └── (protected)/            Route group — authenticated shell
│       ├── layout.tsx          Sidebar + top navigation
│       └── dashboard/
├── components/
│   ├── ui/                     Design system primitives — no business logic
│   └── theme-toggle.tsx        Composed components
├── layouts/                    Page-level structural components
├── providers/                  React context providers
├── lib/                        Client utilities
│   ├── api-client.ts           The single HTTP client
│   ├── env.ts                  Validated environment configuration
│   └── utils.ts                cn() class merger
├── services/                   Data access — API calls plus React Query hooks
├── stores/                     Zustand — UI state only, never server data
├── types/                      Shared TypeScript types
├── tests/e2e/                  Playwright specs
├── public/                     Static assets
└── middleware.ts               Edge middleware — the access-control gate
```

### Naming conventions

| Kind | Convention | Example |
|---|---|---|
| Python modules | `snake_case` | `error_handlers.py` |
| Python classes | `PascalCase` | `TenantScopedRepository` |
| React components | `PascalCase` in `kebab-case.tsx` | `ThemeToggle` in `theme-toggle.tsx` |
| TS functions | `camelCase` | `useUsers` |
| Directories | `kebab-case` | `api-client` |

### Deciding where a new file goes

- Builds SQL → `repositories/`
- Enforces a business rule → `services/`
- Defines a request or response shape → `schemas/`
- Handles HTTP → `api/v1/<domain>/`
- Runs in the background → `tasks/`
- Configures how background work executes → `workers/`
- Pure function with no layer dependencies → `utils/`
- Read by more than one layer and depends on none → `core/`
