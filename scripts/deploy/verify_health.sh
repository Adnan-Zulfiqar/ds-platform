#!/usr/bin/env bash
#
# DropPilot AI — verify a running stack, independently of how it was started.
#
# Separate from deploy.sh so it can be run at any time: after a reboot, during
# an incident, or from a monitor. It asks the stack questions rather than
# reading what Compose believes about it.
set -Eeuo pipefail

readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly DEPLOY_ENV="${DEPLOY_ENV:-/etc/droppilot/deploy.env}"

compose() { docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" "$@"; }
fail() { printf 'UNHEALTHY: %s\n' "$*" >&2; exit 2; }
ok()   { printf '  ok    %s\n' "$*"; }

# --- containers -------------------------------------------------------------
for service in backend frontend worker beat redis rabbitmq cloudflared; do
  state="$(compose ps --format '{{.State}}' "$service" 2>/dev/null || true)"
  [[ "$state" == "running" ]] || fail "$service is '${state:-absent}', not running"
done
ok "every service is running"

# --- application ------------------------------------------------------------
# From inside the network, by service name. Curling the host would prove only
# that something answers on a port — and nothing publishes a port anyway.
compose exec -T backend curl --fail --silent --max-time 10 \
  http://127.0.0.1:8000/health/live >/dev/null || fail "backend liveness failed"
ok "backend /health/live"

compose exec -T backend curl --fail --silent --max-time 15 \
  http://127.0.0.1:8000/health/ready >/dev/null || fail "backend readiness failed"
ok "backend /health/ready — database and Redis reachable"

compose exec -T frontend node -e \
  "fetch('http://127.0.0.1:3000/').then(function(r){process.exit(r.ok?0:1)}).catch(function(){process.exit(1)})" \
  || fail "the frontend did not serve"
ok "frontend serves"

# --- worker -----------------------------------------------------------------
# `inspect ping` round-trips through the broker, so a reply proves the worker is
# consuming rather than merely alive.
compose exec -T worker celery -A app.workers.celery_app.celery_app \
  inspect ping --timeout 15 >/dev/null || fail "the worker did not answer over the broker"
ok "worker answers over RabbitMQ"

# --- schema -----------------------------------------------------------------
revision="$(compose run --rm --entrypoint alembic ops current 2>/dev/null | tail -1)"
printf '  ok    schema at %s\n' "${revision:-unknown}"

# --- exposure ---------------------------------------------------------------
# The check that matters most, asked of the kernel rather than of Compose. `ss`
# sees what is actually bound, including anything a manual `docker run`
# published behind the deployment's back.
if command -v ss >/dev/null 2>&1; then
  exposed="$(ss -ltnH 2>/dev/null | awk '{print $4}' \
    | grep -v '^127\.0\.0\.1' | grep -v '^\[::1\]' \
    | grep -E ':(3000|8000|6379|5672|15672|5432)$' || true)"
  [[ -z "$exposed" ]] || fail "internal ports are bound publicly: ${exposed}"
  ok "no internal port is bound on a public address"
fi

printf '\nHealthy.\n'
