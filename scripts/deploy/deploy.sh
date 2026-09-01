#!/usr/bin/env bash
#
# DropPilot AI — deploy the accepted commit.
#
#   ./scripts/deploy/deploy.sh <accepted-sha>
#
# Order matters and is the whole design:
#
#   1. preflight              — refuses before anything is touched
#   2. record what is running — so a rollback has something to name
#   3. migrate                — must exit 0 or the stack does not start
#   4. up --wait              — health checks must pass, not merely containers exist
#   5. verify                 — an independent check of the running stack
#
# A failure at 3, 4 or 5 leaves the previous images recorded and untouched;
# `rollback.sh` is then a single command. Nothing here deletes an image, a
# volume or a database row.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE
readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly DEPLOY_ENV="${DEPLOY_ENV:-/etc/droppilot/deploy.env}"
readonly PREVIOUS="${PREVIOUS:-/etc/droppilot/images.previous.env}"
readonly IMAGES_ENV="${IMAGES_ENV:-/etc/droppilot/images.env}"

fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 2; }

[[ $# -eq 1 ]] || { printf 'usage: %s <accepted-sha>\n' "$0" >&2; exit 1; }
readonly ACCEPTED_SHA="$1"

compose() { docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" "$@"; }

# --- 1 ----------------------------------------------------------------------
printf '=== preflight ===\n'
"$HERE/preflight.sh" "$ACCEPTED_SHA" || fail "preflight refused"

# --- 2 ----------------------------------------------------------------------
# Copied before anything changes. A rollback that has to guess what was running
# is not a rollback.
if [[ -f "$IMAGES_ENV" ]]; then
  install -m 600 "$IMAGES_ENV" "$PREVIOUS"
  printf '\nPrevious image set preserved at %s\n' "$PREVIOUS"
else
  printf '\nNo previous image set — this appears to be the first deployment.\n'
fi

# --- 3 ----------------------------------------------------------------------
printf '\n=== migrations ===\n'
"$HERE/migrate.sh" || fail "migration failed; the stack was not started"

# --- 4 ----------------------------------------------------------------------
printf '\n=== starting ===\n'
# `--wait` blocks until every service with a health check reports healthy, and
# returns non-zero if any does not. Without it this script would report success
# on a stack that is still crash-looping.
if ! compose up -d --wait --wait-timeout 300; then
  printf '\nThe stack did not become healthy. Recent logs:\n' >&2
  compose logs --tail 40 backend frontend worker >&2 || true
  fail "deployment did not reach a healthy state — run rollback.sh"
fi

# --- 5 ----------------------------------------------------------------------
printf '\n=== verification ===\n'
"$HERE/verify_health.sh" || fail "health verification failed — run rollback.sh"

# --- report -----------------------------------------------------------------
printf '\n=== deployed ===\n'
printf '  commit    %s\n' "$ACCEPTED_SHA"
printf '  at        %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Names, states and image ids only. No environment, no command line, nothing
# that could carry a credential into a deployment log.
compose ps --format '  {{.Service}} {{.State}} {{.Image}}'
printf '\nPublication remains blocked: the Terms are unpublished and no backup\n'
printf 'regime is operational. This deployment changes neither.\n'
