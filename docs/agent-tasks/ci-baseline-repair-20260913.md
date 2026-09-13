# CI baseline repair — security templates and pytest package import

## Manager task
Restore the existing `develop` CI baseline without weakening any guard.

## Scope
Only these files may change:
- `deploy/lightsail/app.env.example`
- `scripts/r7_provision_stack.py`
- `backend/tests/__init__.py` (create if required)
- `backend/tests/conftest.py` only if package initialization alone is insufficient
- this task document only if an implementation note is necessary

## Acceptance criteria
1. `python scripts/check_secrets.py` passes without weakening or editing the scanner.
2. Sensitive values in `deploy/lightsail/app.env.example` remain documented but secret-bearing template assignments flagged by the scanner are blank.
3. `scripts/r7_provision_stack.py` does not contain either published test Fernet key. Generate any synthetic R7 encryption key at runtime using the existing cryptography dependency rather than committing another fixed key.
4. The backend test suite can import `tests.environment` under the CI Python/pytest layout. Prefer the smallest package-layout correction; do not rewrite application imports.
5. No production application behaviour, migrations, workflow permissions, deployment execution, or real credentials are changed.

## Forbidden actions
Do not edit `scripts/check_secrets.py` to silence findings. Do not add allowlists for the flagged files. Do not commit a real or reusable secret. Do not merge, deploy, contact production, or use real provider credentials. Do not modify application source.

## Verification
Trusted CI is authoritative. At minimum the security scanner, backend lint/format/mypy/migrations/pytest, and existing frontend checks must be allowed to run after the bridge pushes the patch.

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
