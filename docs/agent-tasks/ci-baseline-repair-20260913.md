# CI baseline repair — security templates, pytest import, and PostgreSQL CI tooling

## Manager task
Restore the existing `develop` CI baseline without weakening any guard. The security-template and pytest-package fixes are complete. The remaining authoritative CI failure is environmental: the backend job runs PostgreSQL 17.11 but the hosted runner resolves PostgreSQL 16.15 `pg_dump`/`pg_restore`, so the real backup/restore drill aborts on client/server version mismatch.

## Scope
Only these files may change:
- `deploy/lightsail/app.env.example`
- `scripts/r7_provision_stack.py`
- `backend/tests/__init__.py` (create if required)
- `backend/tests/conftest.py` only if package initialization alone is insufficient
- `backend/tests/unit/test_infra_l1_deployment.py` only to align its stale placeholder assertion with the scanner's required blank secret assignments
- `.github/workflows/ci.yml` only to install/select PostgreSQL 17 client tools for the backend CI job so `pg_dump`/`pg_restore` match the existing PostgreSQL 17 service
- this task document only for implementation/verification notes

## Acceptance criteria
1. `python scripts/check_secrets.py` passes without weakening or editing the scanner.
2. Sensitive values in `deploy/lightsail/app.env.example` remain documented but secret-bearing template assignments flagged by the scanner are blank.
3. `scripts/r7_provision_stack.py` does not contain either published test Fernet key. Generate any synthetic R7 encryption key at runtime using the existing cryptography dependency rather than committing another fixed key.
4. The backend test suite can import `tests.environment` under the CI Python/pytest layout. Prefer the smallest package-layout correction; do not rewrite application imports.
5. `test_the_environment_template_contains_no_real_value` recognizes the scanner-required blank secret assignments as valid only when they are empty; it continues requiring `CHANGE-ME` placeholders for the other non-allowlisted template variables.
6. Backend CI keeps the existing PostgreSQL 17 service and runs the real backup/restore drill with PostgreSQL 17 `pg_dump` and `pg_restore`. The fix must verify/select the matching client; do not skip, xfail, mock, or weaken the backup drill.
7. The workflow change is limited to client-tool version parity. Do not change workflow triggers, permissions, service database major version, test commands, security gates, or deployment behavior.
8. No production application behaviour, migrations, deployment execution, or real credentials are changed.

## Forbidden actions
Do not edit `scripts/check_secrets.py` to silence findings. Do not add scanner allowlists for the flagged files. Do not broadly weaken or skip the infra unit test. Do not skip, xfail, mock, or delete the backup/restore drill. Do not modify `app/services/database_backup.py` to tolerate an incompatible PostgreSQL client. Do not downgrade the PostgreSQL 17 CI service to make the runner's old client work. Do not alter GitHub workflow triggers or permissions. Do not commit a real or reusable secret. Do not merge, deploy, contact production, or use real provider credentials. Do not modify application source.

## Verification
Trusted CI is authoritative. Security scanner, backend lint/format/mypy/migrations/full pytest, frontend lint/types/build, and all downstream CI jobs that are enabled after the primary jobs must pass. Cursor performs a final independent read-only review of the exact pushed HEAD.

## Implementation note (retry on head `210176a`)
- `deploy/lightsail/app.env.example`: blanked the four flagged assignments
  (`SECURITY_ENCRYPTION_KEYS`, `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`,
  `ALIEXPRESS_APP_SECRET`) while leaving every surrounding comment intact.
- `scripts/r7_provision_stack.py`: replaced the committed published test
  Fernet key with `Fernet.generate_key()`, generated once per run and used
  only for the disposable reviewer stack's env file.
- `backend/tests/__init__.py`: added so pytest's default import mode roots
  the `tests` package at `backend/` instead of `backend/tests`. That is what
  makes `tests.environment` (imported by `conftest.py`) resolve regardless of
  which files an editable install exposes on `sys.path`. `conftest.py` itself
  did not need to change.

## Cursor follow-up finding on head `9302c16`
Cursor independently confirmed the four-file repair is security-scoped, but found
that `backend/tests/unit/test_infra_l1_deployment.py` still required `CHANGE-ME`
in every non-allowlisted assignment. That contradicted the scanner rule that the
four sensitive assignments above must be empty. The follow-up repair changed
only that unit-test assertion, narrowly: those four secret names assert an empty
value, while the existing `CHANGE-ME` requirement remains intact for all other
variables. A subsequent Ruff-only annotation (`ClassVar`) preserved those semantics.

## Authoritative CI follow-up on head `29a23115`
Run `34761459399` proved Security and Frontend green. Backend Ruff, format, strict
mypy, and Alembic migrations also passed, and full pytest reached all 2,984 tests.
The only failures were 14 setup errors in
`tests/integration/test_backup_restore_drill.py`, all caused by the same tool
mismatch: the `postgres:17-alpine` service reported PostgreSQL 17.11 while the
Ubuntu runner invoked `pg_dump` 16.15. PostgreSQL correctly aborted with
`server version mismatch`. The repair must therefore make the backend CI job use
PostgreSQL 17 client tools, preserving the real drill and the PostgreSQL 17 server.
