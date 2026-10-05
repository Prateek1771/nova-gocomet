import { randomBytes } from "node:crypto";

import Redis from "ioredis";
import { cookies } from "next/headers";
import * as oidc from "openid-client";

import { needsRefresh, SESSION_COOKIE } from "./auth-utils";
import { env } from "./env";
import { oidcConfig } from "./oidc";

/** Server-side session. Tokens live here (Redis) and never reach the browser (ADR-020). */
export type Session = {
  accessToken: string;
  refreshToken: string;
  idToken: string;
  expiresAt: number; // access token expiry, epoch ms
  user: { sub: string; name?: string; email?: string };
};

const TTL_S = 36_000; // = Keycloak SSO max lifespan
const key = (id: string) => `nova:sess:${id}`;

const g = globalThis as unknown as { novaRedis?: Redis };
const redis = (g.novaRedis ??= new Redis(env.redisUrl, { maxRetriesPerRequest: 2, lazyConnect: true }));

export async function createSession(s: Session): Promise<string> {
  const id = randomBytes(32).toString("base64url");
  await redis.set(key(id), JSON.stringify(s), "EX", TTL_S);
  return id;
}

async function load(id: string): Promise<Session | null> {
  const raw = await redis.get(key(id));
  return raw ? (JSON.parse(raw) as Session) : null;
}

export async function destroySession(id: string): Promise<Session | null> {
  const s = await load(id);
  await redis.del(key(id));
  return s;
}

export function fromTokens(t: oidc.TokenEndpointResponse & oidc.TokenEndpointResponseHelpers): Session {
  const claims = t.claims();
  if (!claims || !t.refresh_token || !t.id_token) throw new Error("token response incomplete");
  return {
    accessToken: t.access_token,
    refreshToken: t.refresh_token,
    idToken: t.id_token,
    expiresAt: Date.now() + (t.expires_in ?? 300) * 1000,
    user: { sub: claims.sub, name: claims.name as string | undefined, email: claims.email as string | undefined },
  };
}

/** Current session with a fresh access token, or null. Refreshes (rotating) when < 60 s remain. */
export async function currentSession(): Promise<Session | null> {
  const id = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!id) return null;
  const s = await load(id);
  if (!s || !needsRefresh(s.expiresAt)) return s;
  try {
    const t = await oidc.refreshTokenGrant(oidcConfig(), s.refreshToken);
    const next: Session = {
      ...s,
      accessToken: t.access_token,
      refreshToken: t.refresh_token ?? s.refreshToken,
      idToken: t.id_token ?? s.idToken,
      expiresAt: Date.now() + (t.expires_in ?? 300) * 1000,
    };
    await redis.set(key(id), JSON.stringify(next), "EX", TTL_S);
    return next;
  } catch {
    // ponytail: a parallel request may have rotated the refresh token first; reuse its result.
    // Upgrade to a per-session Redis lock if this race shows up in practice.
    const again = await load(id);
    if (again && !needsRefresh(again.expiresAt)) return again;
    await redis.del(key(id));
    return null;
  }
}
