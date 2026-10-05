import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/auth-utils";

/**
 * Optimistic guard only: no session cookie → login. The real check (Redis session lookup) happens in the
 * (app) layout and the /api/v1 proxy; nova-api authorises every call itself.
 */
export function proxy(req: NextRequest) {
  if (req.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/api/auth/login", req.url);
  login.searchParams.set("returnTo", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico|webp)$).*)"],
};
