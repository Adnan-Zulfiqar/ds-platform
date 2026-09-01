# DropPilot AI — operations and migration runner.
#
# One-off tasks, never a long-running service: Alembic migrations, the PROD-H1
# readiness audit, and (once activation is authorised) the backup tooling.
#
# **Why this is a separate image from the backend.** The backend image is
# deliberately minimal — no `pg_dump`, no `psql`, no Alembic CLI on the path in
# a shape convenient for an operator. Adding those to the service image would
# put database-dumping tools inside the process that faces the internet, which
# is the one place they must not be. Here they are in a container that runs for
# thirty seconds under an operator's hand and then exits.
#
# It shares the backend's dependency build exactly, so a migration is applied by
# the same SQLAlchemy and the same Alembic revision graph the application will
# then use. A separate dependency set is how a migration that "worked" turns out
# to have been run by a different library version.

# ---------------------------------------------------------------------------
# Stage 1 — build dependencies (identical to backend.Dockerfile)
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/pyproject.toml ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# postgresql-client supplies `pg_dump`, `pg_restore` and `psql`. The major
# version is pinned to match the managed server: a client older than the server
# cannot read its custom-format dumps, and discovering that during a recovery is
# the worst possible time.
#
# ca-certificates is not optional here. The managed database is reached with
# `sslmode=verify-full`, and verification against an empty trust store fails
# every connection.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libpq5 \
        postgresql-client-17 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pg_dump --version

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
LABEL org.opencontainers.image.title="droppilot-ops" \
      org.opencontainers.image.revision="${APP_SHA}" \
      org.opencontainers.image.source="https://github.com/Adnan-Zulfiqar/ds-platform" \
      org.opencontainers.image.vendor="Whiteto Ltd" \
      org.opencontainers.image.licenses="UNLICENSED"

STOPSIGNAL SIGTERM

USER droppilot

# No default command that *does* anything. This image exists to be given an
# explicit task — `alembic upgrade head`, `python scripts/verify_production_config.py`
# — and a default of "migrate" would mean starting the container by accident
# migrates the database.
CMD ["python", "-c", "import sys; sys.exit('Give this image an explicit command: alembic, or a script under scripts/.')"]
