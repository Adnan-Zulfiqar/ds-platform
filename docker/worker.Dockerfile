# DropPilot AI — Celery worker image.
#
# Shares the backend's dependency build exactly. The worker imports the same
# application code — services, repositories, models — so a separate dependency
# set would drift and produce bugs that reproduce in one process type only.
#
# Only the entrypoint differs: no HTTP server, no exposed port.

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
RUN mkdir -p app && touch app/__init__.py README.md \
    && pip install --upgrade pip \
    && pip install .

# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 droppilot \
    && useradd --system --uid 1001 --gid droppilot --create-home droppilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=droppilot:droppilot backend/ /app/

USER droppilot

# Celery refuses to run as root by design, so the unprivileged user above is a
# requirement here rather than only good practice.
#
# `--concurrency` is left unset: Celery defaults to the CPU count, and the
# container's CPU limit is the orchestrator's decision.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD celery -A app.workers.celery_app.celery_app inspect ping --destination celery@$HOSTNAME || exit 1

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", "--loglevel=info"]
