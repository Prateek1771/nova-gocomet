import { notFound } from "next/navigation";
import { cache } from "react";

import { env } from "./env";
import { currentSession } from "./session";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Server Component reads from nova-api with the session's bearer token. Client islands use /api/v1. */
export async function apiGet<T>(path: string): Promise<T> {
  const s = await currentSession();
  if (!s) throw new ApiError(401, "unauthenticated", "no session");
  const res = await fetch(`${env.apiUrl}/api/v1${path}`, {
    headers: { authorization: `Bearer ${s.accessToken}`, accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code: string; message: string } } | null;
    throw new ApiError(res.status, body?.error?.code ?? "error", body?.error?.message ?? res.statusText);
  }
  return (await res.json()) as T;
}

/** A detail read where "not found" (incl. another tenant's id under RLS) renders the 404 page. */
export async function apiGetOrNotFound<T>(path: string): Promise<T> {
  try {
    return await apiGet<T>(path);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
}

export type Me = {
  sub: string;
  email: string | null;
  name: string | null;
  tenant: { id: string; slug: string; name: string } | null;
  roles: string[];
};

/** Deduped per request: layout and page both need it. */
export const getMe = cache(() => apiGet<Me>("/me"));
