#!/usr/bin/env bash
#
# DropPilot AI — the deferred Linux rehearsal, as a runnable script.
#
#   ./scripts/deploy/rehearse_linux.sh <expected-sha> <rehearsal-database-name>
#
# **This script existing is not the rehearsal happening.** It is the entry point
# for running it later, on a Linux host or a Lightsail staging instance, once
# one is available. Until it has been run and its evidence file filled in, the
# rehearsal is NOT EXECUTED and remains a mandatory pre-deployment gate.
#
# ---------------------------------------------------------------------------
# What it will not do
# ---------------------------------------------------------------------------
#
# It never touches production, DNS, Google, Resend, the Terms, the backup
# profile or systemd. Those are not omissions to be filled in later — the
# refusals below are the reason this can be handed to someone else to run.
#
# It defaults to refusal: every required input is checked before anything is
# built, and a missing one stops the run rather than being guessed.
#
# Reuses the existing deployment scripts rather than restating them:
# `preflight.sh` owns configuration validation and `verify_health.sh` owns the
# health checks. Two authorities that drift is how a rehearsal comes to prove
# something the deployment does not do.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE
readonly REPO="$(cd "$HERE/../.." && pwd)"
readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly EVIDENCE_TEMPLATE="docs/operations/rehearsal-evidence.template.md"

fail() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
step() { printf '\n=== %s ===\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }

[[ $# -eq 2 ]] || {
  printf 'usage: %s <expected-sha> <rehearsal-database-name>\n' "$0" >&2
  printf '  the database name must NOT be a protected production name\n' >&2
  exit 1
}
readonly EXPECTED_SHA="$1"
readonly REHEARSAL_DB="$2"

cd "$REPO"

# ---------------------------------------------------------------------------
# 1. Refusals, before anything is built
# ---------------------------------------------------------------------------
step "refusals"

[[ "$(uname -s)" == "Linux" ]] \
  || fail "this rehearsal must run on Linux; the point is to exercise the platform the deployment targets, and a Docker Desktop VM on another OS is not it"
ok "running on Linux"

command -v docker >/dev/null 2>&1 || fail "docker is not on PATH"
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
  || fail "the Docker daemon is not answering"
ok "Docker daemon is answering"

[[ -z "$(git status --porcelain)" ]] || fail "the working tree has uncommitted changes"
actual="$(git rev-parse HEAD)"
[[ "$actual" == "$EXPECTED_SHA" ]] || fail "HEAD is ${actual} but ${EXPECTED_SHA} was expected"
ok "clean tree at the expected commit"

# Protected database names, compared the way BACKUP-B1-R1 compares them:
# case-insensitively and with surrounding whitespace trimmed, because a
# protection you can step over by holding shift is decoration.
trimmed="$(printf '%s' "$REHEARSAL_DB" | tr -d '[:space:]')"
folded="$(printf '%s' "$trimmed" | tr '[:upper:]' '[:lower:]')"
[[ -n "$folded" ]] || fail "the rehearsal database name is empty"
case "$folded" in
  droppilot) fail "'${REHEARSAL_DB}' is a protected production database name" ;;
esac
ok "rehearsal database '${REHEARSAL_DB}' is not a protected name"

# Production hostnames. A rehearsal that reached the real Cloudflare hostnames
# would be a deployment with a different name on it.
for forbidden in app.whiteto.com api.whiteto.com auth.whiteto.com whiteto.com; do
  if [[ "${POSTGRES_HOST:-}" == *"$forbidden"* ]] \
     || [[ "${NEXT_PUBLIC_API_URL:-}" == *"$forbidden"* ]] \
     || [[ "${REHEARSAL_DB}" == *"$forbidden"* ]]; then
    fail "the environment references the production hostname ${forbidden}"
  fi
done
case "${POSTGRES_HOST:-}" in
  *rds.amazonaws.com|*.amazonaws.com) fail "POSTGRES_HOST points at a managed AWS database; the rehearsal uses an isolated container" ;;
esac
ok "no production hostname is referenced"

: "${REHEARSAL_ENV_FILE:?set REHEARSAL_ENV_FILE to an isolated environment file with synthetic secrets}"
[[ -f "$REHEARSAL_ENV_FILE" ]] || fail "REHEARSAL_ENV_FILE does not exist"
grep -q '^ENVIRONMENT=' "$REHEARSAL_ENV_FILE" || fail "the rehearsal environment file declares no ENVIRONMENT"
ok "isolated environment file supplied"

# The backup profile is never selected. Stated here as well as omitted, so that
# adding it later is a visible edit rather than an oversight.
readonly COMPOSE_PROFILES=""
export COMPOSE_PROFILES
ok "backup profile not selected"

# ---------------------------------------------------------------------------
# 2. Build, without cache
# ---------------------------------------------------------------------------
step "building images (no cache)"
# `--no-cache` because a rehearsal that reuses layers proves the layers, not the
# build. This is the run that has to answer "does a clean checkout build".
for image in backend worker ops; do
  docker build --no-cache --file "docker/${image}.Dockerfile" \
    --tag "droppilot-${image}:rehearsal" \
    --build-arg "APP_SHA=${EXPECTED_SHA}" . \
    || fail "the ${image} image did not build"
done
docker build --no-cache --file docker/frontend.Dockerfile \
  --tag droppilot-frontend:rehearsal \
  --build-arg "APP_SHA=${EXPECTED_SHA}" \
  --build-arg "NEXT_PUBLIC_API_URL=http://localhost:8000" \
  --build-arg "NEXT_PUBLIC_ENVIRONMENT=staging" . \
  || fail "the frontend image did not build"

step "image digests and sizes"
for image in backend worker ops frontend; do
  docker image inspect "droppilot-${image}:rehearsal" \
    --format '  {{.RepoTags}} {{.Id}} {{.Size}} bytes revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
done

# ---------------------------------------------------------------------------
# 3. Scans
# ---------------------------------------------------------------------------
step "vulnerability and secret scans"
if command -v trivy >/dev/null 2>&1; then
  for image in backend worker ops frontend; do
    trivy image --severity HIGH,CRITICAL --exit-code 0 "droppilot-${image}:rehearsal" || true
  done
else
  printf '  NOT RUN: trivy is not installed. Record this honestly in the evidence file.\n'
fi

# A secret baked into a layer is visible in the build history for anyone who can
# pull the image. This greps what the layers actually record.
for image in backend worker ops frontend; do
  if docker history --no-trunc "droppilot-${image}:rehearsal" \
       | grep -iE 'SECRET_KEY=|PASSWORD=|API_KEY=|-----BEGIN'; then
    fail "a secret appears in the ${image} image history"
  fi
done
ok "no secret in any image history"

# ---------------------------------------------------------------------------
# 4. Start the isolated stack
# ---------------------------------------------------------------------------
step "starting the isolated stack"
compose() { docker compose --env-file "$REHEARSAL_ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

compose config >/dev/null || fail "the Compose file does not render"
ok "Compose renders"

compose up -d --wait --wait-timeout 300 || {
  compose logs --tail 50 >&2 || true
  fail "the stack did not become healthy"
}

step "proving nothing is published"
if ss -ltnH 2>/dev/null | awk '{print $4}' \
     | grep -v '^127\.0\.0\.1' | grep -v '^\[::1\]' \
     | grep -E ':(3000|8000|6379|5672|15672|5432)$'; then
  fail "an internal port is bound on a public address"
fi
ok "no internal port is bound publicly"

# ---------------------------------------------------------------------------
# 5. Migration, health, restart
# ---------------------------------------------------------------------------
step "migration 0029 -> 0032 on the isolated database"
compose run --rm --entrypoint alembic ops current || true
compose up --exit-code-from migrate migrate || fail "the migration failed"
compose run --rm --entrypoint alembic ops current

step "health"
"$HERE/verify_health.sh" || fail "health verification failed"

step "graceful restart and dependency recovery"
compose restart backend
compose up -d --wait --wait-timeout 180 || fail "the stack did not recover after a backend restart"
ok "backend restarted and became healthy"

compose stop rabbitmq
sleep 5
compose start rabbitmq
compose up -d --wait --wait-timeout 240 || fail "the stack did not recover after a broker outage"
ok "worker reconnected after a broker outage"

# ---------------------------------------------------------------------------
# 6. Playwright
# ---------------------------------------------------------------------------
step "focused Playwright against the Linux stack"
if [[ -d frontend/node_modules ]]; then
  ( cd frontend && npx playwright test --reporter=line ) || fail "Playwright failed"
else
  printf '  NOT RUN: frontend/node_modules is absent. Record this honestly.\n'
fi

# ---------------------------------------------------------------------------
# 7. Clean up only what this run created
# ---------------------------------------------------------------------------
step "cleanup"
# `down` with volumes, because the rehearsal's volumes are the rehearsal's. No
# `docker system prune`, no image pruning: removing something this run did not
# create is how a rehearsal deletes a colleague's work.
compose down --volumes --remove-orphans
for image in backend worker ops frontend; do
  docker image rm "droppilot-${image}:rehearsal" >/dev/null 2>&1 || true
done
ok "removed only the containers, volumes and images this run created"

printf '\n=== rehearsal complete ===\n'
printf 'Fill in %s with the output above.\n' "$EVIDENCE_TEMPLATE"
printf 'Until that file records a real run, the rehearsal is NOT EXECUTED.\n'
