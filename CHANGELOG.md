# Changelog

All notable changes to DropPilot AI.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses phase tags rather than semantic versions until the first
production release.

---

## [Unreleased]

Phase 2 — scope not yet defined. No feature work.

### Added

- `CLAUDE.md` — the engineering constitution for this repository
- `docs/TECHNICAL_DEBT.md` — ranked debt register, each item with a trigger
- **Login throttle test coverage** (16 tests). The throttle previously had none,
  while a fixture docstring claimed otherwise. Runs against `fakeredis` so it
  executes everywhere rather than skipping without a Redis server
- **Authorization integration tests** (9 tests) covering each role, a token with
  no roles, a token with only unrecognised roles, and that authorization is
  decided before resource lookup so a 403 does not leak existence

### Changed

- Both `/api/v1/users` endpoints now require a recognised role via
  `RequireViewer`. `require_minimum_role` was previously unit-tested but wired
  to no endpoint
- Sidebar entries for unbuilt destinations render as disabled "Soon" items
  instead of linking to routes that returned 404
- Development branch renamed from `phase-0-foundation` to `develop`

### Fixed

- `frontend/tsconfig.tsbuildinfo` is no longer tracked in git
- Corrected a docstring in `tests/integration/conftest.py` that claimed test
  coverage which did not exist

---

## [phase-1-complete] — 2026-07-31

Authentication and multi-tenant identity. Commit `a21f7b8`.

### Added

**Authentication**
- Argon2id password hashing, with NFKC normalisation so the same password typed
  on different platforms verifies, and transparent rehashing when parameters are
  raised
- Password strength validation: minimum 12 characters, common-password denylist,
  and rejection of passwords containing the user's own email local part
- JWT access tokens (15 minutes) and refresh tokens (30 days), with the `typ`
  claim verified on every decode so a refresh token cannot be used as a bearer
  credential
- Refresh token rotation with reuse detection — replaying a consumed token
  terminates every session for that user
- `AuthenticatedUser` principal bound to context variables, feeding tenant
  filtering in every repository
- Role-based authorization: `require_roles` for exact membership and
  `require_minimum_role` for hierarchical checks
- Login throttling counted against email **and** client IP independently,
  checked before password verification
- Minimum length enforced on the JWT signing key (32 characters, RFC 7518 §3.2)
  in every environment

**API endpoints**
- `POST /api/v1/auth/register` — creates tenant, first user, and owner role
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout` — requires no access token
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/me` — roles read from the database, not the token

**Database** — migration `0002`
- `roles` table, seeded with four roles using deterministic UUIDv5 identifiers
  so a role has the same id in every environment
- `user_roles` association table with a composite primary key
- `refresh_tokens` table storing only a SHA-256 hash of each token

**Frontend**
- Sign-in, registration, and password-reset request pages
- `AuthProvider` session management; access token held in memory only, refresh
  token in an httpOnly path-scoped cookie
- Single-flight token refresh in the API client — without it, concurrent 401s
  each rotate the token, the second presents a consumed one, and reuse detection
  signs the user out
- `AuthGuard` route protection distinguishing *loading* from *unauthenticated*,
  so a page refresh does not eject an authenticated user
- Form primitives wiring react-hook-form and Zod to the design system with
  correct `aria-describedby` / `aria-invalid` handling

**Testing**
- 25 integration tests running against real PostgreSQL, building the schema by
  applying the Alembic migrations rather than `create_all`
- 53 new unit tests covering token verification, password handling, and
  authorization

**Documentation**
- `docs/Authentication.md`, `docs/Database.md`

### Changed

- **Tenant identity now comes from a verified JWT claim** instead of the
  `X-Tenant-ID` header. Confined to `app/api/deps.py`; no endpoint, service, or
  repository signature moved
- `users` gains `first_name`, `last_name`, `is_verified`
- `Base.__mapper_args__` sets `eager_defaults=True`, required in an async
  codebase — see Fixed below
- Enum columns pass `values_callable` so member values are persisted, not names
- `docs/DevelopmentSetup.md` rewritten; its `X-Tenant-ID` instructions had
  become actively wrong

### Removed

Each required by the Phase 1 specification. Safe because migration `0001` had
never been applied to any live database.

- `users.full_name` — replaced by `first_name` and `last_name`, with a read-only
  property preserving the display form
- `users.email_verified_at` — replaced by `is_verified`
- `users.role` enum column and the `user_role` type — replaced by `user_roles`.
  Keeping both would give a user's role two sources of truth, and reading the
  stale one is a privilege-escalation bug
- The Phase 0 `UserRole` **enum** was renamed to `RoleName` so the `UserRole`
  **table** could take the name

### Fixed

Three bugs found by running against real PostgreSQL. None was reachable without
a live database, and all three would have reached production.

- **Enum persistence.** SQLAlchemy sent member *names* (`"TRIAL"`) where the
  database expected values (`"trial"`), failing every tenant insert
- **`MissingGreenlet` on login.** Server-side `onupdate` columns are expired
  after a flush; serialising a just-updated user triggered lazy IO, which raises
  in async SQLAlchemy. This broke *every* login
- **No minimum JWT signing key length.** A short key signs and verifies
  normally and is only weaker, so the weakness was entirely silent

---

## [phase-0-complete] — 2026-07-30

Foundation. Commit `b513e4b`.

### Added

- Clean architecture with a strictly one-way dependency direction:
  `api → services → repositories → models`, with `core` depending on nothing
- Multi-tenancy enforced in a single base repository, reading the tenant from
  context so it cannot be forgotten at a call site
- UUID primary keys, UTC timestamps, soft deletes, deterministic constraint
  names, connection pooling
- Global exception handling producing one error envelope for every failure, with
  a request id on every response
- Structured logging with correlation across API and workers
- Redis caching with tenant-namespaced keys; distributed rate limiting with a
  circuit breaker
- Celery and RabbitMQ configured with context propagation and no jobs
- Next.js 15 / React 19 frontend, design system on CSS-variable tokens, light
  and dark themes
- Multi-stage non-root Dockerfiles, Compose stack, Nginx reverse proxy, GitHub
  Actions
- 54 tests, 11 of them targeting tenant isolation directly
- `README`, `Architecture`, `FolderStructure`, `CodingStandards`,
  `DevelopmentSetup`, `Contributing`

### Changed

- `app.workers.tasks` moved to `app.tasks` during finalisation. Tasks are entry
  points, architecturally symmetric with `api/`; `workers/` retains the
  infrastructure that runs them

### Fixed

- Pydantic `ClassVar` error that made the application unimportable
- Missing `email-validator` dependency
- Rate limiter paying a full Redis connect timeout on every request during an
  outage, adding ~4 seconds of latency per request

---

[Unreleased]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-1-complete...HEAD
[phase-1-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-0-complete...phase-1-complete
[phase-0-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/releases/tag/phase-0-complete
