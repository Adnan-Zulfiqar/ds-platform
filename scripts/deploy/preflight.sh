#!/usr/bin/env bash
#
# DropPilot AI — deployment preflight. Reads, checks, and changes nothing.
#
#   ./scripts/deploy/preflight.sh <accepted-sha>
#
# This is the gate every other deployment script assumes has passed. It runs
# first, it runs on its own, and it exits non-zero the moment anything is not as
# expected — before an image is built, before a container starts, before a
# migration touches a schema.
#
# **Nothing here writes.** That is the point: an operator who is unsure whether
# to deploy can run this as often as they like, and its answer is the same
# answer the deployment would have reached, obtained without doing any of it.
#
# Exit codes
#   0  ready to deploy
#   1  usage
#   2  configuration, identity or repository state refused
#   3  a required tool or file is missing
set -Eeuo pipefail

readonly COMPOSE_FILE="docker/compose.lightsail.yml"
readonly DEPLOY_ENV="${DEPLOY_ENV:-/etc/droppilot/deploy.env}"
readonly APP_ENV="${APP_ENV:-/etc/droppilot/app.env}"

fail()  { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
missing() { printf 'MISSING: %s\n' "$*" >&2; exit 3; }
ok()    { printf '  ok    %s\n' "$*"; }
note()  { printf '  note  %s\n' "$*"; }

[[ $# -eq 1 ]] || { printf 'usage: %s <accepted-sha>\n' "$0" >&2; exit 1; }
readonly ACCEPTED_SHA="$1"

printf 'DropPilot deployment preflight\n\n'

# ---------------------------------------------------------------------------
# 1. Tools
# ---------------------------------------------------------------------------
for tool in git docker; do
  command -v "$tool" >/dev/null 2>&1 || missing "$tool is not on PATH"
done
docker compose version >/dev/null 2>&1 || missing "docker compose v2 is not available"
ok "git, docker and docker compose are present"

# ---------------------------------------------------------------------------
# 2. Repository state
# ---------------------------------------------------------------------------
# A deployment must be reproducible from a commit somebody reviewed. Deploying a
# dirty tree produces an artefact that exists nowhere in history, so when it
# misbehaves there is nothing to diff against.
[[ -z "$(git status --porcelain)" ]] || fail "the working tree has uncommitted changes"
ok "working tree is clean"

actual_sha="$(git rev-parse HEAD)"
[[ "$actual_sha" == "$ACCEPTED_SHA" ]] \
  || fail "HEAD is ${actual_sha} but ${ACCEPTED_SHA} was accepted"
ok "HEAD is the accepted commit ${ACCEPTED_SHA:0:12}"

# ---------------------------------------------------------------------------
# 3. Configuration files exist, and are not readable by everyone
# ---------------------------------------------------------------------------
for f in "$DEPLOY_ENV" "$APP_ENV"; do
  [[ -f "$f" ]] || missing "$f"
  mode="$(stat -c '%a' "$f")"
  # 600 or tighter. A secret file the whole instance can read is a secret every
  # process on the instance has, including anything that gets a shell.
  [[ "$mode" == "600" || "$mode" == "400" ]] \
    || fail "$f has mode $mode; it must be 600 (root-owned, root-readable)"
  owner="$(stat -c '%U' "$f")"
  [[ "$owner" == "root" ]] || fail "$f is owned by $owner, not root"
done
ok "environment files exist, are root-owned and mode 600"

parent="$(dirname "$APP_ENV")"
dir_mode="$(stat -c '%a' "$parent")"
[[ "$dir_mode" == "700" ]] || fail "$parent has mode $dir_mode; it must be 700"
ok "$parent is mode 700"

# ---------------------------------------------------------------------------
# 4. No secret has leaked into the repository
# ---------------------------------------------------------------------------
# The deployment directory is outside Git by design. This checks the design held
# — someone copying a working config into the checkout "just to test" is how it
# stops holding.
if git ls-files --error-unmatch "$(basename "$APP_ENV")" >/dev/null 2>&1; then
  fail "$(basename "$APP_ENV") is tracked in Git"
fi
ok "no deployment environment file is tracked"

# ---------------------------------------------------------------------------
# 5. Compose renders, and renders safely
# ---------------------------------------------------------------------------
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" config > "$rendered" 2>/dev/null \
  || fail "the Compose file does not render — a mandatory variable is missing"
ok "Compose renders with the supplied environment"

# The single most consequential check in this script. A published port on this
# instance is reachable from the internet the moment the firewall is edited by
# anyone, and the tunnel makes every one of them unnecessary.
if grep -qE '^\s+published:' "$rendered"; then
  printf '\n'; grep -nE -B4 '^\s+published:' "$rendered" >&2
  fail "a service publishes a host port; nothing may, the tunnel is the only ingress"
fi
ok "no service publishes a host port"

grep -q 'privileged: true' "$rendered" && fail "a service runs privileged"
grep -q 'network_mode: host' "$rendered" && fail "a service uses host networking"
ok "no privileged container and no host networking"

# A development server in production. `--reload` re-execs on file change and
# runs a single unsupervised process; `next dev` serves unminified source maps.
if grep -qE -- '--reload|next dev|npm run dev' "$rendered"; then
  fail "a service is configured to run a development server"
fi
ok "no development server or hot reload"

# ---------------------------------------------------------------------------
# 6. Images are pinned by digest
# ---------------------------------------------------------------------------
# A tag is a moving reference. `droppilot-backend:latest` today and tomorrow can
# be different images, which makes "roll back to what was running" impossible to
# state precisely. A digest cannot move.
while IFS= read -r image; do
  [[ "$image" == *"@sha256:"* ]] \
    || fail "image ${image} is not pinned by digest"
done < <(grep -oP '(?<=^    image: ).*' "$rendered" | grep -E 'droppilot-')
ok "every application image is pinned by digest"

# ---------------------------------------------------------------------------
# 7. PROD-H1 readiness
# ---------------------------------------------------------------------------
printf '\nRunning the production-readiness audit...\n'
set +e
docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" --profile ops \
  run --rm readiness
readiness_code=$?
set -e

case "$readiness_code" in
  0) ok "configuration is ready to deploy" ;;
  2) fail "the configuration is not fit to deploy — see the findings above" ;;
  3) note "publication is blocked (Terms and/or backups), which is expected today"
     note "the application will start; it will refuse registrations while Terms are unpublished" ;;
  *) fail "the readiness audit exited ${readiness_code}" ;;
esac

# ---------------------------------------------------------------------------
# 8. Backup readiness — reported honestly, never asserted
# ---------------------------------------------------------------------------
printf '\n'
note "BACKUPS: no production backup regime is operational. The readiness audit"
note "         reports this as BLOCKED and that is correct — no key, no off-site"
note "         destination and no drill from a production backup exist yet."
note "         Deploying is permitted; going live is not. See BACKUP_RUNBOOK.md §8."

printf '\nPreflight passed. Nothing was changed.\n'
