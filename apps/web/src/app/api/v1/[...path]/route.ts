import type { NextRequest } from "next/server";

import { upstreamPath } from "@/lib/auth-utils";
import { env } from "@/lib/env";
import { currentSession } from "@/lib/session";

export const dynamic = "force-dynamic";

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
const REQ_HEADERS = ["accept", "content-type", "last-event-id", "x-request-id", "if-none-match"];
const RES_HEADERS = ["content-type", "cache-control", "x-request-id", "etag", "content-disposition", "www-authenticate"];

function error(status: number, code: string, message: string) {
  return Response.json({ error: { code, message, details: [] }, request_id: null }, { status });
}

/** Same-origin BFF proxy to nova-api: attaches the bearer token and streams the body back (SSE too). */
async function handler(req: NextRequest, ctx: RouteContext<"/api/v1/[...path]">) {
  // CSRF: mutations must come from our own origin (cookie is SameSite=Lax as a second layer)
  if (!SAFE.has(req.method) && req.headers.get("origin") !== new URL(env.appUrl).origin) {
    return error(403, "forbidden", "bad origin");
  }
  const session = await currentSession();
  if (!session) return error(401, "unauthenticated", "no session");

  const path = upstreamPath((await ctx.params).path);
  if (!path) return error(400, "bad_request", "invalid path");

  const headers = new Headers({ authorization: `Bearer ${session.accessToken}` });
  for (const h of REQ_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const upstream = await fetch(new URL(path + req.nextUrl.search, env.apiUrl), {
    method: req.method,
    headers,
    body: SAFE.has(req.method) ? undefined : req.body,
    // @ts-expect-error -- Node fetch needs duplex for a streamed request body; not in the DOM types
    duplex: "half",
    signal: req.signal,
    redirect: "manual",
    cache: "no-store",
  });

  const out = new Headers();
  for (const h of RES_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { handler as DELETE, handler as GET, handler as PATCH, handler as POST, handler as PUT };
