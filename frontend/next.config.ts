import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Emit a minimal standalone server bundle. The Docker image copies only that
  // output, which cuts the runtime image from ~1GB to ~150MB and removes
  // node_modules — and its published vulnerabilities — from the running
  // container.
  output: "standalone",

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
    // The CSP names only what Google Identity Services genuinely needs, and
    // each origin is there for one reason:
    //
    //   script-src   accounts.google.com  — the GIS client library itself
    //   style-src    accounts.google.com  — the stylesheet that script loads
    //   frame-src    accounts.google.com  — the account chooser it opens
    //   connect-src  accounts.google.com  — the calls GIS makes while signing in
    //   img-src      *.googleusercontent.com — avatars on the account chooser
    //
    // `style-src` was missing in the first cut of this header, and the browser
    // blocked `accounts.google.com/gsi/style` — Google's button drew unstyled.
    // Caught by a console-error assertion in the wider E2E run, not by review.
    //
    // No wildcard on `google.com`: that would cover every Google property
    // including user-controlled content hosts. `frame-ancestors 'none'` keeps
    // the X-Frame-Options guarantee in a form modern browsers still honour.
    //
    // `'unsafe-inline'` on styles is Next.js's own requirement for its injected
    // critical CSS; it is deliberately **not** granted to scripts, which is the
    // half that matters for XSS.
    const apiOrigin = new URL(
      process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
    ).origin;

    const csp = [
      "default-src 'self'",
      "base-uri 'self'",
      "object-src 'none'",
      "frame-ancestors 'none'",
      "form-action 'self'",
      "script-src 'self' 'unsafe-inline' https://accounts.google.com",
      "style-src 'self' 'unsafe-inline' https://accounts.google.com",
      "img-src 'self' data: https://*.googleusercontent.com",
      "font-src 'self' data:",
      `connect-src 'self' ${apiOrigin} https://accounts.google.com`,
      "frame-src https://accounts.google.com",
    ].join("; ");

    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
    ];
  },
};

export default nextConfig;
