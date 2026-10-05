import { NextResponse, type NextRequest } from "next/server";
import * as oidc from "openid-client";

import { SESSION_COOKIE } from "@/lib/auth-utils";
import { env } from "@/lib/env";
import { oidcConfig } from "@/lib/oidc";
import { destroySession } from "@/lib/session";

export const dynamic = "force-dynamic";

/** POST only (a form in the shell), same-origin checked; ends the Redis session, then the Keycloak SSO session. */
export async function POST(req: NextRequest) {
  if (req.headers.get("origin") !== new URL(env.appUrl).origin) {
    return NextResponse.json({ error: { code: "forbidden", message: "bad origin", details: [] } }, { status: 403 });
  }
  const sid = req.cookies.get(SESSION_COOKIE)?.value;
  const s = sid ? await destroySession(sid) : null;
  const target = s
    ? oidc.buildEndSessionUrl(oidcConfig(), { id_token_hint: s.idToken, post_logout_redirect_uri: `${env.appUrl}/` })
    : new URL(`${env.appUrl}/`);
  const res = NextResponse.redirect(target, 303);
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
