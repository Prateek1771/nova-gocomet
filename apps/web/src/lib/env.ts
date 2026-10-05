// Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_*).
function req(name: string, fallback?: string): string {
  const v = process.env[name] ?? fallback;
  if (!v) throw new Error(`missing env ${name}`);
  return v;
}

export const env = {
  appUrl: req("APP_URL", "http://localhost:3300"),
  // issuer as the browser sees it (must equal the token `iss`)
  issuer: req("KEYCLOAK_ISSUER", "http://localhost:8180/realms/nova"),
  // same realm as reached from this server (differs inside docker compose)
  issuerInternal: req("KEYCLOAK_INTERNAL_ISSUER", process.env.KEYCLOAK_ISSUER ?? "http://localhost:8180/realms/nova"),
  clientId: req("OIDC_CLIENT_ID", "nova-web"),
  clientSecret: req("OIDC_CLIENT_SECRET", "nova-web-dev-secret"),
  apiUrl: req("NOVA_API_URL", "http://localhost:8100"),
  redisUrl: req("REDIS_URL", "redis://localhost:6379"),
};

export const secureCookies = env.appUrl.startsWith("https://");
