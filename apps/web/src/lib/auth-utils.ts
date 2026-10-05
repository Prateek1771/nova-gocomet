// Pure helpers (no I/O) so they can be unit tested without Redis or Keycloak.

export const SESSION_COOKIE = "nova_sid";
export const LOGIN_COOKIE = "nova_oidc";

/** One login-state cookie per in-flight login (keyed by OIDC state), so parallel tabs don't clobber each other. */
export function loginCookieName(state: string): string {
  return `${LOGIN_COOKIE}_${state.replace(/[^A-Za-z0-9_-]/g, "")}`;
}

export const REFRESH_MARGIN_MS = 60_000;

/** Refresh the access token when less than 60 s of it remain (LLD §10). */
export function needsRefresh(expiresAt: number, now = Date.now()): boolean {
  return expiresAt - now < REFRESH_MARGIN_MS;
}

/** Only same-origin relative paths; blocks open redirects like `//evil.com` or `https://…`. */
export function safeReturnTo(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  return value;
}

/** Upstream API path from catch-all segments; rejects dot segments so `..` can't escape /api/v1. */
export function upstreamPath(segments: string[]): string | null {
  if (segments.some((s) => s === "." || s === ".." || s === "")) return null;
  return "/api/v1/" + segments.map(encodeURIComponent).join("/");
}
