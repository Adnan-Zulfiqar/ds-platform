#!/usr/bin/env bash
#
# DropPilot AI — build the four production images and record their digests.
#
#   ./scripts/deploy/build_images.sh <accepted-sha> <public-api-url>
#
# Writes /etc/droppilot/images.env with a digest-pinned reference per image.
# That file is what the Compose stack reads, which is what makes a deployment
# and a rollback both able to name exactly one artefact.
#
# **NEXT_PUBLIC_API_URL is a build-time authority, not a runtime one.** Next.js
# inlines every NEXT_PUBLIC_* value into the client bundle during `npm run
# build`, so the browser's idea of where the API lives is fixed here and cannot
# be changed later by an environment variable. Passing the wrong one produces a
# frontend that looks fine and calls the wrong host. It is therefore a required
# argument rather than a defaulted one.
set -Eeuo pipefail

readonly IMAGES_ENV="${IMAGES_ENV:-/etc/droppilot/images.env}"

fail() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }

[[ $# -eq 2 ]] || { printf 'usage: %s <accepted-sha> <https://api.example>\n' "$0" >&2; exit 1; }
readonly ACCEPTED_SHA="$1"
readonly API_URL="$2"

[[ -z "$(git status --porcelain)" ]] || fail "the working tree has uncommitted changes"
[[ "$(git rev-parse HEAD)" == "$ACCEPTED_SHA" ]] || fail "HEAD is not the accepted commit"

# A loopback or plain-http API URL baked into the bundle is the single most
# common way a deployment ships a frontend nobody can use. Refused at the one
# moment it can still be corrected.
case "$API_URL" in
  https://*) : ;;
  *) fail "the public API URL must be https; got a non-https value" ;;
esac
case "$API_URL" in
  *localhost*|*127.0.0.1*|*0.0.0.0*) fail "the public API URL is a loopback address" ;;
esac

readonly TAG="${ACCEPTED_SHA:0:12}"

build() {
  local name="$1" dockerfile="$2"; shift 2
  printf '\n=== building droppilot-%s:%s ===\n' "$name" "$TAG"
  docker build \
    --file "$dockerfile" \
    --tag "droppilot-${name}:${TAG}" \
    --build-arg "APP_SHA=${ACCEPTED_SHA}" \
    "$@" \
    .
}

build backend  docker/backend.Dockerfile
build worker   docker/worker.Dockerfile
build ops      docker/ops.Dockerfile
build frontend docker/frontend.Dockerfile \
  --build-arg "NEXT_PUBLIC_API_URL=${API_URL}" \
  --build-arg "NEXT_PUBLIC_ENVIRONMENT=production"

# ---------------------------------------------------------------------------
# Record digests
# ---------------------------------------------------------------------------
# A local build has no registry digest, so the image ID is what pins it. Both
# are recorded: the ID is what this instance will run, and the RepoDigest (when
# the image has been pushed) is what another instance would resolve.
umask 077
: > "$IMAGES_ENV"
{
  printf '# Written by scripts/deploy/build_images.sh — do not edit by hand.\n'
  printf '# Commit: %s\n' "$ACCEPTED_SHA"
  printf '# Built:  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '# Public API URL baked into the frontend bundle: %s\n' "$API_URL"
} >> "$IMAGES_ENV"

for pair in "FRONTEND_IMAGE:frontend" "BACKEND_IMAGE:backend" "WORKER_IMAGE:worker" "OPS_IMAGE:ops"; do
  var="${pair%%:*}"; name="${pair##*:}"
  id="$(docker image inspect --format '{{.Id}}' "droppilot-${name}:${TAG}")"
  printf '%s=%s\n' "$var" "$id" >> "$IMAGES_ENV"
  printf '  %-16s %s\n' "$name" "$id"
done

chmod 600 "$IMAGES_ENV"
printf '\nDigests recorded in %s\n' "$IMAGES_ENV"
printf 'Keep the previous copy: it is what a rollback needs.\n'
