# Contributing

## Development process

This project is built in phases. Each phase is a defined scope, and work stays
inside it:

- Implement only what the current phase specifies.
- Preserve existing functionality unless a change is explicitly requested.
- Never change APIs, folder structures, naming conventions, database schemas, or
  architectural decisions without explaining why the change is required.
- When a phase forces a change to earlier code, make the minimum necessary
  change and keep backward compatibility where possible.
- Extend existing systems rather than creating parallel ones.

## Branching

```
main            always deployable
├── feat/...    new functionality
├── fix/...     bug fixes
├── chore/...   tooling, dependencies
└── docs/...    documentation
```

Branch names are lowercase and hyphenated: `feat/aliexpress-product-import`.

## Commits

Conventional Commits:

```
<type>(<scope>): <subject>

<body — why, not what>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

Subject is imperative and present tense — "add", not "added". The body explains
the reasoning when it is not obvious from the diff.

## Before opening a pull request

Backend:

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Also confirm:

- New or changed behaviour has tests.
- Any new tenant-scoped repository has an isolation test.
- Documentation is updated in the same PR — docs that lag the code stop being
  trusted, and once distrusted they stop being read.
- No `.env`, secret, or credential is staged.
- Migrations have a working `downgrade()`.

## Pull requests

Keep them small and single-purpose. A 2,000-line PR gets rubber-stamped; a
200-line PR gets reviewed.

Describe **what** changed, **why**, and **how it was verified**. State
explicitly what you could not verify — an untested path flagged in the
description is a known risk, whereas the same path presented as working is a
trap for whoever hits it next.

## Review expectations

Reviewers check, in order:

1. **Correctness** — does it do what it claims?
2. **Tenant isolation** — can any new query see another tenant's rows?
3. **Layer discipline** — SQL only in repositories, no HTTP below the API layer.
4. **Security** — input validated, nothing sensitive logged or returned.
5. **Tests** — does a test actually fail if the behaviour regresses?
6. **Clarity** — will this be readable in a year?

Comments should say what to change and why. "This leaks other tenants' rows
because `_base_query` is bypassed" is actionable; "this looks wrong" is not.

## Reporting a problem

Include: what you did, what you expected, what happened, and the `requestId`
from the error response — it maps directly to the server-side log line.
