import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "storeops_session";

/**
 * Optimistic routing only: visitors without a session cookie are sent to /login before the app
 * shell renders. Real authorisation happens in the API on every request; an expired session is
 * handled client-side when the API answers 401.
 */
export function proxy(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const { pathname, search } = request.nextUrl;
  const url = new URL("/login", request.url);
  url.searchParams.set("next", pathname + search);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/app/:path*", "/onboarding"],
};
