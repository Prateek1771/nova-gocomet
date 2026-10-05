import * as oidc from "openid-client";

import { env } from "./env";

let config: oidc.Configuration | undefined;

/**
 * Explicit metadata instead of discovery: the browser reaches Keycloak at the public issuer while this
 * server reaches it at the internal one, and discovery would reject the issuer mismatch.
 */
export function oidcConfig(): oidc.Configuration {
  if (config) return config;
  const pub = `${env.issuer}/protocol/openid-connect`;
  const internal = `${env.issuerInternal}/protocol/openid-connect`;
  config = new oidc.Configuration(
    {
      issuer: env.issuer,
      authorization_endpoint: `${pub}/auth`, // browser
      end_session_endpoint: `${pub}/logout`, // browser
      token_endpoint: `${internal}/token`, // server → Keycloak
      jwks_uri: `${internal}/certs`,
    },
    env.clientId,
    env.clientSecret,
  );
  if (env.issuerInternal.startsWith("http://") || env.issuer.startsWith("http://")) {
    oidc.allowInsecureRequests(config); // dev only: Keycloak on plain http
  }
  return config;
}

export const SCOPES = "openid profile email organization";
