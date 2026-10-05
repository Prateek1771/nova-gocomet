import { NextResponse, type NextRequest } from "next/server";
import * as oidc from "openid-client";

import { loginCookieName, safeReturnTo, SESSION_COOKIE } from "@/lib/auth-utils";
import { env, secureCookies } from "@/lib/env";
import { oidcConfig } from "@/lib/oidc";
import { createSession, destroySession, fromTokens } from "@/lib/session";

export const dynamic = "force-dynamic";

type LoginState = { verifier: string; nonce: string; returnTo: string };

export async function GET(req: NextRequest) {
  const state = req.nextUrl.searchParams.get("state") ?? "";
  const cookieName = loginCookieName(state);
  const raw = state ? req.cookies.get(cookieName)?.value : undefined;
  // unknown/expired login (old tab, >10 min on the form): start over; Keycloak's SSO usually finishes it at once
  if (!raw) return NextResponse.redirect(`${env.appUrl}/api/auth/login`);
  const login = JSON.parse(raw) as LoginState;

  // the URL Keycloak redirected to, as the browser saw it (request.url may carry the container host)
  const current = new URL(`${env.appUrl}/api/auth/callback${req.nextUrl.search}`);
  let tokens;
  try {
    tokens = await oidc.authorizationCodeGrant(oidcConfig(), current, {
      pkceCodeVerifier: login.verifier,
      expectedState: state,
      expectedNonce: login.nonce,
      idTokenExpected: true,
    });
  } catch (e) {
    console.error(JSON.stringify({ msg: "oidc callback failed", error: String(e) }));
    const res = NextResponse.json({ error: { code: "login_failed", message: "login failed", details: [] } }, { status: 400 });
    res.cookies.delete({ name: cookieName, path: "/api/auth" });
    return res;
  }

  const old = req.cookies.get(SESSION_COOKIE)?.value;
  if (old) await destroySession(old); // finishing a login in a second tab replaces, not leaks, the first
  const sid = await createSession(fromTokens(tokens));
  const res = NextResponse.redirect(`${env.appUrl}${safeReturnTo(login.returnTo)}`);
  res.cookies.delete({ name: cookieName, path: "/api/auth" });
  res.cookies.set(SESSION_COOKIE, sid, { httpOnly: true, secure: secureCookies, sameSite: "lax", path: "/", maxAge: 36_000 });
  return res;
}
