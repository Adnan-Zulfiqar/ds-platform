# DropPilot AI — backend API image.
#
# Multi-stage: dependencies are installed in a builder and only the resulting
# virtualenv is copied forward. The runtime image therefore has no compiler
# toolchain, which removes both ~400MB and a large share of the CVEs that a
# build-capable image carries.

# ---------------------------------------------------------------------------
# Stage 1 — build dependencies
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential and libpq-dev are needed to compile psycopg/asyncpg wheels
# when no prebuilt wheel matches. Present only in this stage.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only the dependency manifest first. Docker caches this layer, so editing
# application code does not reinstall every dependency — the difference between
# a 5-second and a 3-minute rebuild.
COPY backend/pyproject.toml ./
COPY backend/requirements/runtime.txt ./requirements/runtime.txt
COPY backend/scripts/check_dependency_lock.py ./scripts/check_dependency_lock.py

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Refuse to build from a stale lock. Someone adds a dependency, forgets to
# recompile, and without this the image builds happily from the old resolution —
# missing the package they just added. Nothing fails until runtime, in the
# environment furthest from the person who made the change.
RUN python scripts/check_dependency_lock.py --only runtime

# `--require-hashes` is what makes this a frozen install rather than a fresh
# resolution that happens to look similar. Every requirement must be pinned to
# an exact version *and* match a recorded hash, so pip cannot pick up a newer
# patch release, and a substituted artefact on the index is refused rather than
# installed.
#
# `--no-deps` because the lock already holds the full transitive closure.
# Letting pip resolve dependencies of pinned packages is how a version outside
# the lock gets in.
#
# The application package itself is never installed — only its dependencies.
# Installing it would leave a stub `app` package in site-packages that shadows
# the real code for any command run from another directory.
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements/runtime.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq5 is the runtime half of libpq-dev. curl is required by the healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. A container process running as root that is
# compromised through an application vulnerability is trivially easier to
# escalate from, and root is never required to serve HTTP on port 8000.
RUN groupadd --system --gid 1001 droppilot \
    && useradd --system --uid 1001 --gid droppilot --create-home droppilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=droppilot:droppilot backend/ /app/


# ---------------------------------------------------------------------------
# Provenance. `APP_SHA` is passed by `scripts/deploy/build_images.sh` from the
# commit being deployed, so a running container can always be traced back to a
# reviewed commit — `docker inspect` answers "what is actually running", which
# is the first question in every incident.
# ---------------------------------------------------------------------------
ARG APP_SHA=unknown
LABEL org.opencontainers.image.title="droppilot-backend"       org.opencontainers.image.revision="${APP_SHA}"       org.opencontainers.image.source="https://github.com/Adnan-Zulfiqar/ds-platform"       org.opencontainers.image.vendor="Whiteto Ltd"       org.opencontainers.image.licenses="UNLICENSED"

# SIGTERM is what Docker and Compose send on stop. uvicorn handles it and drains
# in-flight requests; the default SIGTERM is therefore correct and is stated
# rather than left implicit, because changing it silently breaks graceful
# shutdown.
STOPSIGNAL SIGTERM

USER droppilot

EXPOSE 8000

# Readiness rather than liveness: the orchestrator needs to know this instance
# can serve traffic, not merely that the process exists.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health/ready || exit 1

# No --reload and no --workers. Process count is the orchestrator's decision
# (scale replicas), not the image's; baking in workers double-multiplies
# concurrency and exhausts the database connection pool.
#
# --no-proxy-headers: uvicorn would otherwise rewrite scope["client"] from
# X-Forwarded-For for any peer in its own trusted list, giving the deployment
# two competing proxy-trust authorities. The application's resolver
# (`app.core.client_ip`, driven by SECURITY_TRUSTED_PROXIES) is the only one.
# See docs/PRODUCTION_SECURITY.md section 7.
CMD ["uvicorn", "app.main:app", "--no-proxy-headers", "--host", "0.0.0.0", "--port", "8000"]
