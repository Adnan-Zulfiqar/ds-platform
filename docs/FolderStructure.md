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
│   ├── workers/                Celery application and base task
│   │   └── tasks/              Task implementations (empty in Phase 0)
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

### Deviations from the originally specified layout

Three, each recorded here because folder structure is not changed silently.

| Specified | Implemented | Reason |
|---|---|---|
| `config/` at top level | `core/config.py` | A dedicated package for one settings module adds a directory without adding clarity. Configuration is a cross-cutting concern and `core` is where those live. |
| `exceptions/` at top level | `core/exceptions.py` | The exception hierarchy is imported by every layer including `core` itself. Placing it inside `core` keeps the dependency graph acyclic and makes "core depends on nothing" literally true. |
| `tasks/` at top level | `workers/tasks/` | Tasks are meaningless without the Celery app that registers them. Nesting keeps a task and its runtime configuration together. |

If you prefer the original layout, all three are mechanical moves plus an import
update — say so and they will be changed.

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
- Runs in the background → `workers/tasks/`
- Pure function with no layer dependencies → `utils/`
- Read by more than one layer and depends on none → `core/`
