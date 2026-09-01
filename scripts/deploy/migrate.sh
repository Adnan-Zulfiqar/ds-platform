#!/usr/bin/env bash
#
# DropPilot AI — apply Alembic migrations, once, with the identity checked.
#
#   ./scripts/deploy/migrate.sh
#
# Runs the one-off `migrate` service. The application services depend on this
# container having exited zero, so a failed migration keeps the stack down
# rather than letting it serve against a half-migrated schema.
#
# **This does not take a backup.** It cannot: no production backup regime is
# operational yet (BACKUP_RUNBOOK.md §8). Until one is, the rollback story for a
# migration is the migration's own `downgrade()` and nothing else, which is why
# the revision before and after are both recorded here — that pair is what a
# recovery would need.
set -Eeuo pipefail

readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly DEPLOY_ENV="${DEPLOY_ENV:-/etc/droppilot/deploy.env}"

fail() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }

compose() { docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" "$@"; }

printf 'Revision before: '
before="$(compose run --rm --entrypoint alembic ops current 2>/dev/null | tail -1)"
printf '%s\n' "${before:-<none>}"

printf '\nApplying migrations...\n'
if ! compose up --exit-code-from migrate migrate; then
  fail "the migration failed. The stack has not been started. The database may be
         at an intermediate revision — check 'alembic current' before retrying."
fi

printf '\nRevision after:  '
after="$(compose run --rm --entrypoint alembic ops current 2>/dev/null | tail -1)"
printf '%s\n' "${after:-<none>}"

printf '\nRecord this pair. It is the rollback checkpoint:\n'
printf '  from %s\n  to   %s\n' "${before:-<none>}" "${after:-<none>}"
