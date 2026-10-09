"use client";

/** Browser-side calls go through the BFF proxy (/api/v1): same origin, cookie session, no token in JS. */
export class ClientError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown[] = [],
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, { ...init, headers: { accept: "application/json", ...init?.headers } });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code: string; message: string; details?: unknown[] } } | null;
    throw new ClientError(res.status, body?.error?.code ?? "error", body?.error?.message ?? res.statusText, body?.error?.details);
  }
  return (await res.json()) as T;
}

export const postJson = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
