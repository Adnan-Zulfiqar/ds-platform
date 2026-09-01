#!/usr/bin/env bash
#
# DropPilot AI — return to the previously deployed image set.
#
#   ./scripts/deploy/rollback.sh
#
# **What this does and does not do.** It puts the previous *images* back —
# frontend and backend together, always — and restarts the stack. It does not
# revert the database, and it cannot: an Alembic downgrade that drops a column
# destroys the data in it, and there is no production backup to restore from
# (docs/operations/BACKUP_RUNBOOK.md §8).
#
# So the schema question has to be answered before running this, and the script
# asks it out loud rather than assuming. A rollback across a migration that only
# *added* things is safe — the old code ignores the new column. A rollback
# across one that changed or removed something is not, and needs a decision no
# script should make on an operator's behalf.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE
readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly DEPLOY_ENV="${DEPLOY_ENV:-/etc/droppilot/deploy.env}"
readonly PREVIOUS="${PREVIOUS:-/etc/droppilot/images.previous.env}"
readonly IMAGES_ENV="${IMAGES_ENV:-/etc/droppilot/images.env}"

fail() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
compose() { docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" "$@"; }

[[ -f "$PREVIOUS" ]] || fail "no previous image set at $PREVIOUS — there is nothing to roll back to"

printf 'Rolling back to:\n'
grep -E '^(FRONTEND|BACKEND|WORKER|OPS)_IMAGE=' "$PREVIOUS" | sed 's/^/  /'
printf '\nRecorded commit: %s\n' "$(grep -m1 '^# Commit:' "$PREVIOUS" | cut -d' ' -f3-)"

# --- schema compatibility ---------------------------------------------------
current_revision="$(compose run --rm --entrypoint alembic ops current 2>/dev/null | tail -1)"
printf '\nThe database is at revision: %s\n' "${current_revision:-unknown}"
printf 'The previous images expect a schema at or below that revision.\n\n'
printf 'If the deployment being rolled back applied a migration that REMOVED or\n'
printf 'CHANGED a column, the old code may fail against this schema. Adding-only\n'
printf 'migrations are safe to roll back across.\n\n'

if [[ ! -t 0 ]]; then
  fail "a rollback needs a typed confirmation and this session has no terminal"
fi
read -r -p "Type rollback to continue: " typed
[[ "$typed" == "rollback" ]] || fail "not confirmed; nothing was changed"

# --- swap -------------------------------------------------------------------
# Frontend and backend move together. A frontend built against one API contract
# talking to a backend serving another is a broken deployment that reports
# itself healthy.
if [[ -f "$IMAGES_ENV" ]]; then
  install -m 600 "$IMAGES_ENV" "${IMAGES_ENV}.rolled-back-from"
fi
install -m 600 "$PREVIOUS" "$IMAGES_ENV"

printf '\nRestarting with the previous images...\n'
if ! compose up -d --wait --wait-timeout 300; then
  compose logs --tail 40 backend frontend >&2 || true
  fail "the previous image set did not become healthy either — this is an incident"
fi

"$HERE/verify_health.sh"

printf '\nRolled back. The replaced image set is kept at %s.rolled-back-from\n' "$IMAGES_ENV"
printf 'Nothing was deleted.\n'
