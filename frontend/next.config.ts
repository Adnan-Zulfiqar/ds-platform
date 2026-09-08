import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Emit a minimal standalone server bundle. The Docker image copies only that
  // output, which cuts the runtime image from ~1GB to ~150MB and removes
  // node_modules — and its published vulnerabilities — from the running
  // container.
  output: "standalone",

  // Isolated acceptance builds can set NEXT_DIST_DIR (for example `.next-r6`).
  distDir: process.env.NEXT_DIST_DIR ?? ".next",

  // Do not advertise the framework and version to every client.
  poweredByHeader: false,

  eslint: {
    // Linting runs as its own CI job. Running it again inside `next build`
    // doubles the work and conflates two different failures in one log.
    ignoreDuringBuilds: true,
  },

  typescript: {
    // Never true. A type error must fail the build — suppressing it here is how
    // a broken deploy reaches production looking green.
    ignoreBuildErrors: false,
  },

  async headers() {
    // Defence in depth: Nginx sets these at the edge too, but a direct-to-Node
    // deployment or a local `next start` would otherwise be unprotected.
    //
    // The Content-Security-Policy is deliberately **not** here. It carries a
    // per-request nonce now, and a value in this file is fixed at build time —
    // one nonce baked into every response is not a nonce, it is a password an
    // attacker reads out of any page. It is built in `middleware.ts` instead;
    // see `lib/csp.ts` for the directives and the reasoning behind each origin.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
