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

# Install the *dependencies only*, never the application package itself.
#
# An earlier version created a stub `app/__init__.py` so that `pip install .`
# would succeed without the real source. That worked, but it left an empty `app`
# package in site-packages which the real code at /app only shadows because the
# working directory precedes site-packages on sys.path. Any command run from a
# different directory resolved to the empty stub and failed with a confusing
# ImportError.
#
# Extracting the dependency list keeps the layer cache — it changes only when
# pyproject.toml changes — without installing anything that can shadow the
# application. `tomllib` is in the standard library from Python 3.11.
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
#
# --no-proxy-headers: uvicorn would otherwise rewrite scope["client"] from
# X-Forwarded-For for any peer in its own trusted list, giving the deployment
# two competing proxy-trust authorities. The application's resolver
# (`app.core.client_ip`, driven by SECURITY_TRUSTED_PROXIES) is the only one.
# See docs/PRODUCTION_SECURITY.md section 7.
CMD ["uvicorn", "app.main:app", "--no-proxy-headers", "--host", "0.0.0.0", "--port", "8000"]
