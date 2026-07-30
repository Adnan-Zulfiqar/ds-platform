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

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# `pip install .` needs the package present; a stub keeps this layer independent
# of the real source so the cache survives code changes.
RUN mkdir -p app && touch app/__init__.py README.md \
    && pip install --upgrade pip \
    && pip install .

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

USER droppilot

EXPOSE 8000

# Readiness rather than liveness: the orchestrator needs to know this instance
# can serve traffic, not merely that the process exists.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health/ready || exit 1

# No --reload and no --workers. Process count is the orchestrator's decision
# (scale replicas), not the image's; baking in workers double-multiplies
# concurrency and exhausts the database connection pool.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
