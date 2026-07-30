import { NextResponse, type NextRequest } from "next/server";

/**
 * Edge middleware — the single gate for route access.
 *
 * Runs before any page renders, which is what makes it the correct place for
 * access control: a check inside a layout or component runs *after* the route
 * has already begun rendering, and can be bypassed by a direct fetch of the
 * RSC payload.
 *
 * **Phase 0 does not enforce anything.** Authentication does not exist, so
 * there is no session to check and every route is reachable. The file exists
 * with the matcher and the route classification already correct so that the
 * auth phase changes one function body rather than introducing a new
 * cross-cutting concern late.
 */

/** Routes reachable without a session. */
const PUBLIC_ROUTES = ["/login", "/register", "/forgot-password"] as const;

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;

  // --- Enforcement, enabled by the auth phase -----------------------------
  //
  // const session = request.cookies.get("session");
  //
  // if (!session && !isPublicRoute(pathname)) {
  //   const loginUrl = new URL("/login", request.url);
  //   // Preserve the destination so the user lands where they intended after
  //   // signing in, rather than always on the dashboard.
  //   loginUrl.searchParams.set("next", pathname);
  //   return NextResponse.redirect(loginUrl);
  // }
  //
  // if (session && isPublicRoute(pathname)) {
  //   return NextResponse.redirect(new URL("/dashboard", request.url));
  // }
  // ------------------------------------------------------------------------

  // Referenced so the helper and the route table are covered by the type
  // checker and linter until enforcement is switched on.
  void isPublicRoute(pathname);

  return NextResponse.next();
}

export const config = {
  /**
   * Match every path except static assets and image optimisation.
   *
   * Running middleware on static files would add latency to every asset request
   * for no benefit. `_next/static` and `_next/image` are served directly.
   */
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
