import { NextResponse, type NextRequest } from "next/server";

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

/** Reachable without a session. */
const PUBLIC_ROUTES = ["/login", "/register", "/forgot-password"] as const;

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;

  // `/` is a redirect stub with nothing to protect; sending it to the guard
  // would cost a render before the redirect it was always going to perform.
  if (pathname === "/") {
    return NextResponse.next();
  }

  if (isPublicRoute(pathname)) {
    return NextResponse.next();
  }

  // Everything else is protected. The page renders its own loading state while
  // `AuthProvider` resolves the session, then either shows the content or
  // redirects to sign-in. Redirecting here instead would bounce every
  // authenticated user to the login page on each hard navigation, because the
  // session cookie is invisible at this layer.
  const response = NextResponse.next();

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
