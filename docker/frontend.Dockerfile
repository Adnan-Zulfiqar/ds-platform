# DropPilot AI — frontend image.
#
# Three stages. The final image contains only Next.js standalone output — no
# node_modules, no source, no build tooling — which takes it from roughly 1.2GB
# to under 200MB.

# ---------------------------------------------------------------------------
# Stage 1 — dependencies
# ---------------------------------------------------------------------------
FROM node:22-alpine AS deps

# Alpine's musl libc lacks some symbols Node native addons expect; libc6-compat
# provides the shims.
RUN apk add --no-cache libc6-compat

WORKDIR /app

# Lockfile only, for a cacheable layer. `npm ci` installs exactly what the
# lockfile pins — unlike `npm install`, it will not silently resolve a newer
# version and produce a build that differs from the one that was tested.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# ---------------------------------------------------------------------------
# Stage 2 — build
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

# NEXT_PUBLIC_* values are inlined into the client bundle at build time, so they
# must be present now — supplying them at runtime has no effect. This is why the
# image is environment-specific and why nothing secret may be passed here.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_ENVIRONMENT=production
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_ENVIRONMENT=${NEXT_PUBLIC_ENVIRONMENT} \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3 — runtime
# ---------------------------------------------------------------------------
FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# `standalone` bundles a minimal server plus only the node_modules actually
# reachable from the traced imports.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/dashboard').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
