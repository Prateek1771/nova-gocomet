import { NextResponse, type NextRequest } from "next/server";
import * as oidc from "openid-client";

import { loginCookieName, safeReturnTo, SESSION_COOKIE } from "@/lib/auth-utils";
import { env, secureCookies } from "@/lib/env";
import { oidcConfig, SCOPES } from "@/lib/oidc";
import { destroySession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Starts auth code + PKCE against Keycloak. PKCE verifier, state and nonce ride in a short httpOnly cookie
 * keyed by state. Called while already signed in = "switch user": end our session and Keycloak's SSO
 * session (otherwise Keycloak silently logs the same user back in), then come back here for a fresh form.
 */
export async function GET(req: NextRequest) {
  const returnTo = safeReturnTo(req.nextUrl.searchParams.get("returnTo"));
  const sid = req.cookies.get(SESSION_COOKIE)?.value;
  const previous = sid ? await destroySession(sid) : null;
  if (previous) {
    const again = `${env.appUrl}/api/auth/login?returnTo=${encodeURIComponent(returnTo)}`;
    const res = NextResponse.redirect(
      oidc.buildEndSessionUrl(oidcConfig(), { id_token_hint: previous.idToken, post_logout_redirect_uri: again }),
    );
    res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  const verifier = oidc.randomPKCECodeVerifier();
  const state = oidc.randomState();
  const nonce = oidc.randomNonce();
  const url = oidc.buildAuthorizationUrl(oidcConfig(), {
    redirect_uri: `${env.appUrl}/api/auth/callback`,
    scope: SCOPES,
    code_challenge: await oidc.calculatePKCECodeChallenge(verifier),
    code_challenge_method: "S256",
    state,
    nonce,
  });
  const res = NextResponse.redirect(url);
  if (sid) res.cookies.delete(SESSION_COOKIE); // stale cookie (session already gone from Redis)
  res.cookies.set(
    loginCookieName(state),
    JSON.stringify({ verifier, nonce, returnTo }),
    { httpOnly: true, secure: secureCookies, sameSite: "lax", path: "/api/auth", maxAge: 600 },
  );
  return res;
}
