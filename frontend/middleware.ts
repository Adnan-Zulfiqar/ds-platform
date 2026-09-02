import { NextResponse, type NextRequest } from "next/server";

import { buildContentSecurityPolicy, createNonce } from "@/lib/csp";

/**
 * Edge middleware — the first routing gate.
 *
 * **This is a user-experience gate, not a security boundary.** The security
 * boundary is the API: every `/api/v1` endpoint verifies a signed access token
 * server-side, and no data reaches the browser without it. Everything here only
 * decides which page to render, so bypassing it reveals an empty shell that
 * cannot load any data.
 *
 * That distinction matters because of a real constraint: the refresh token
 * cookie is httpOnly and scoped to the API's path (`/api/v1/auth`). When the
 * frontend and API are served from one origin — the production setup, behind
 * Nginx — the cookie is not sent to page requests because of the path scope.
 * In local development they are on different ports, so it is not sent at all.
 *
 * Middleware therefore cannot reliably see whether a visitor has a session, and
 * pretending otherwise would produce redirect loops. It handles only the cheap,
 * always-correct cases; the authoritative client-side check lives in
 * `app/(protected)/layout.tsx`, which knows the real session state.
 */

/**
 * Reachable without a session.
 *
 * `/unauthorized` is public because it reports a *permission* failure, not an
 * authentication one. Treating it as protected would mean an unauthorized user
 * gets bounced to sign-in, re-enters correct credentials, and lands back on the
 * same wall.
 */
const PUBLIC_ROUTES = [
  "/login",
  "/register",
  "/forgot-password",
  "/unauthorized",
  // The privacy policy has to be readable by someone with no account — eBay
  // fetches it to validate the RuName, and a policy behind a login is not a
  // published policy.
  "/privacy",
  // The same reasoning: a contract nobody can read before signing up is not a
  // published contract, and the registration page links to it.
  "/terms",
] as const;

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

/**
 * Mint a nonce, hand it to Next.js, and put the policy on the way out.
 *
 * The policy goes on the **request** headers as well as the response. That is
 * not belt and braces: it is the documented mechanism by which Next.js finds
 * the nonce and stamps it onto the script tags it renders. Without the request
 * header the response header would be a policy that blocks the application's
 * own hydration.
 */
function withCsp(request: NextRequest): NextResponse {
  const nonce = createNonce();
  const policy = buildContentSecurityPolicy(nonce);

  const requestHeaders = new Headers(request.headers);
  // Never trust an inbound value: a client that sent its own `x-nonce` or CSP
  // header would otherwise choose the nonce for its own injected script.
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const response = withCsp(request);

  // `/` is a public marketing homepage with statutory disclosures; public legal
  // routes are listed in `PUBLIC_ROUTES` below.
  if (pathname === "/" || isPublicRoute(pathname)) {
    return response;
  }

  // Everything else is protected. The page renders its own loading state while
  // `AuthProvider` resolves the session, then either shows the content or
  // redirects to sign-in. Redirecting here instead would bounce every
  // authenticated user to the login page on each hard navigation, because the
  // session cookie is invisible at this layer.
  //
  // Protected pages are user-specific and must never be cached by a shared
  // proxy or served from the browser's back-forward cache after sign-out.
  response.headers.set("Cache-Control", "no-store, must-revalidate");
  return response;
}

export const config = {
  /**
   * Match every path except static assets and image optimisation.
   *
   * Running middleware on static files would add latency to every asset request
   * for no benefit.
   */
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
