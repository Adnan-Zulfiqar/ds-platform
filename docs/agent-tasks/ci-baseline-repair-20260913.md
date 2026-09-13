# CI baseline repair — security templates and pytest package import

## Manager task
Restore the existing `develop` CI baseline without weakening any guard.

## Scope
Only these files may change:
- `deploy/lightsail/app.env.example`
- `scripts/r7_provision_stack.py`
- `backend/tests/__init__.py` (create if required)
- `backend/tests/conftest.py` only if package initialization alone is insufficient
- `backend/tests/unit/test_infra_l1_deployment.py` only to align its stale placeholder assertion with the scanner's required blank secret assignments
- this task document only if an implementation note is necessary

## Acceptance criteria
1. `python scripts/check_secrets.py` passes without weakening or editing the scanner.
2. Sensitive values in `deploy/lightsail/app.env.example` remain documented but secret-bearing template assignments flagged by the scanner are blank.
3. `scripts/r7_provision_stack.py` does not contain either published test Fernet key. Generate any synthetic R7 encryption key at runtime using the existing cryptography dependency rather than committing another fixed key.
4. The backend test suite can import `tests.environment` under the CI Python/pytest layout. Prefer the smallest package-layout correction; do not rewrite application imports.
5. `test_the_environment_template_contains_no_real_value` must recognize the scanner-required blank secret assignments as valid only when they are empty; it must continue requiring `CHANGE-ME` placeholders for the other non-allowlisted template variables.
6. No production application behaviour, migrations, workflow permissions, deployment execution, or real credentials are changed.

## Forbidden actions
Do not edit `scripts/check_secrets.py` to silence findings. Do not add scanner allowlists for the flagged files. Do not broadly weaken or skip the infra unit test. Do not commit a real or reusable secret. Do not merge, deploy, contact production, or use real provider credentials. Do not modify application source.

## Verification
Trusted CI is authoritative. At minimum the security scanner, backend lint/format/mypy/migrations/pytest, and existing frontend checks must pass after the bridge pushes the patch. Cursor performs a final independent read-only review of the exact pushed HEAD.

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
that `backend/tests/unit/test_infra_l1_deployment.py` still requires `CHANGE-ME`
in every non-allowlisted assignment. That contradicts the scanner rule that the
four sensitive assignments above must be empty. The follow-up repair may change
only that unit-test assertion, narrowly: those four secret names must assert an
empty value, while the existing `CHANGE-ME` requirement remains intact for all
other variables.
