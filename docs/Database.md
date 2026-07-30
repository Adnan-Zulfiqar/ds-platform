# Database

Schema, conventions, and the reasoning behind them. Current as of Phase 1
(migration `0002`).

## Conventions

Defined once in `app/models/base.py` and inherited by every table.

| Convention | Rationale |
|---|---|
| **UUID primary keys** | Sequential integers leak business volume and make cross-tenant enumeration trivial. UUIDs also exist before the INSERT, so a service can build an object graph without a round trip. |
| **UTC timestamps, database-generated** | `TIMESTAMP WITH TIME ZONE` with `server_default=now()`. Application servers drift and may sit in different regions; the database is the single authority on time. |
| **Soft deletes** | `deleted_at IS NULL` means live. Customers delete by mistake and financial records must stay auditable. Hard deletion is reserved for GDPR erasure. |
| **Deterministic constraint names** | Without `NAMING_CONVENTION`, Alembic generates names that vary between versions, producing migrations that cannot be reliably downgraded. |
| **`tenant_id` on business tables** | The multi-tenancy discriminator, enforced by `TenantScopedRepository`. |

### `eager_defaults`

`Base.__mapper_args__` sets `eager_defaults=True`. This is **mandatory in an
async codebase, not an optimisation.**

Columns with `server_default` or `onupdate` are expired after a flush. Touching
one afterwards triggers a lazy refresh, which needs IO — and implicit IO in
async SQLAlchemy raises `MissingGreenlet` rather than awaiting. Concretely:
updating a row and then serialising it through a response schema crashes.

That is exactly the shape of `AuthService.login`, which stamps `last_login_at`
and then returns the user. It was found by an integration test running against
a real database; no unit test would have caught it.

### Native enums

Enum columns pass `values_callable` so SQLAlchemy persists the member **value**
(`"trial"`), not the member **name** (`"TRIAL"`), which is its default and does
not match the lowercase labels the migrations create.

Omitting it fails at runtime with `invalid input value for enum tenant_status:
"TRIAL"` on every insert. Values are also the right choice semantically: they
are what appears in the API and the database, so renaming a Python member should
not silently rewrite stored data.

---

## Tables

### `tenants` — the root of the tenancy hierarchy

The billable account. Sits above the tenancy boundary, so it carries no
`tenant_id` of its own.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `name` | varchar(255) | Display name |
| `slug` | varchar(63) | Unique. DNS label — becomes the subdomain. `CHECK` enforces the format |
| `status` | `tenant_status` | `trial` / `active` / `suspended` / `cancelled` |
| `is_active` | boolean | Denormalised access switch, checked on every request |
| `timezone` | varchar(64) | IANA name; reports run in the customer's local day |
| `default_currency` | char(3) | ISO 4217, `CHECK` on length |
| `created_at` / `updated_at` / `deleted_at` | timestamptz | |

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `tenant_id` | UUID | FK → `tenants.id`, `ON DELETE CASCADE` |
| `email` | varchar(320) | RFC 5321 max. Stored lowercased |
| `first_name` / `last_name` | varchar(128) | Nullable |
| `password_hash` | varchar(255) | **Nullable** — an invited or SSO user has no local password. Argon2id PHC string |
| `is_active` | boolean | Can this account authenticate |
| `is_verified` | boolean | Email confirmed. Currently set true on registration; see limitation below |
| `last_login_at` | timestamptz | |

Unique on **`(tenant_id, email)`**, not on `email` alone: the same person may
legitimately hold accounts in two tenants, and a global unique index would
wrongly prevent it.

Soft-deleted users still occupy their address within the tenant — freeing it
would let a re-invited address collide with audit history.

### `roles` — platform-global reference data

No `tenant_id`. Every tenant draws from the same four roles, which keeps
authorization a name comparison and avoids seeding four rows per signup. Seeded
by migration `0002` with **deterministic UUIDv5 identifiers**, so a role has the
same id in every environment and fixtures are portable.

| Role | Rank |
|---|---|
| `owner` | 30 |
| `admin` | 20 |
| `member` | 10 |
| `viewer` | 0 |

### `user_roles` — assignment

Composite primary key `(user_id, role_id)`: the pair *is* the identity, which
enforces "a user holds a role at most once" without a separate constraint. Both
FKs cascade.

**No soft delete.** Revoking a role deletes the row. A soft-deleted assignment
that some query forgot to filter would grant a privilege that was explicitly
revoked — the one direction in which this table must never fail.

A secondary index on `(role_id, user_id)` serves "who holds this role"; the PK
already serves "what roles does this user hold", which runs on every login.

### `refresh_tokens`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | FK → `users.id`, cascade |
| `token_hash` | char(64) | **SHA-256 hex digest. The token itself is never stored** |
| `expires_at` | timestamptz | |
| `revoked_at` | timestamptz | NULL means live. Set on logout, rotation, and reuse detection |
| `created_at` | timestamptz | |

No `updated_at` — a row is written once and only ever revoked. No `tenant_id`:
the lookup happens *before* identity is established, so a tenant filter would
have to be satisfied before it could be derived. Isolation comes from the FK to
a user, who belongs to exactly one tenant.

SHA-256 rather than Argon2 because the token is 256 bits of cryptographic
randomness with no guessable structure; memory-hard hashing buys nothing against
that and would add latency to every session renewal.

---

## Relationships

```
tenants ─┬─< users ─┬─< user_roles >─ roles
         │          └─< refresh_tokens
         │
         └── (future business tables all carry tenant_id)
```

Every FK from a tenant-owned row cascades on tenant deletion, so a genuine
purge removes the customer's data rather than orphaning it.

---

## Migrations

| Revision | Contents |
|---|---|
| `0001` | Initial tenancy schema: `tenants`, `users` |
| `0002` | Authentication: `roles` (seeded), `user_roles`, `refresh_tokens`; `users` gains `first_name`, `last_name`, `is_verified` and drops `full_name`, `email_verified_at`, `role` |

Both have been **applied and verified against PostgreSQL 17**, forwards and
backwards. `0002`'s downgrade reassembles `full_name` from the name parts before
dropping them, so a rollback does not silently discard data.

### Rules

* Never edit a migration that has been applied anywhere. Add a new one.
* Every migration needs a working `downgrade()`. An irreversible migration turns
  a bad deploy into an incident.
* Review autogenerated migrations by hand: Alembic misses enum changes and can
  emit a destructive drop-and-create for what is really a rename.
* Drop enum types explicitly. `drop_table` leaves them behind, and the orphan
  collides with the next upgrade.

```bash
alembic upgrade head
```

```bash
alembic revision --autogenerate -m "add products table"
```

```bash
alembic downgrade -1
```

---

## Known limitations

1. **`is_verified` is set true on registration.** There is no mail delivery to
   verify against yet. When the verification flow lands, the default becomes
   false and registration sends a confirmation.

2. **No partial index on soft deletes.** `WHERE deleted_at IS NULL` is currently
   served by ordinary composite indexes. A partial index would be smaller and
   faster once tables grow.

3. **`refresh_tokens` grows unbounded.** `purge_expired()` exists but nothing
   calls it — it needs the scheduled-job infrastructure that arrives with the
   first real Celery tasks.

4. **Roles cannot be customised per tenant.** Deliberate: custom roles need a
   permission model to attach to. A nullable `tenant_id` on `roles` will let
   global and tenant-defined roles coexist without migrating existing rows.
