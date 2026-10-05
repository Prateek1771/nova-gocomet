"""Keycloak access-token validation (LLD §7a). Authorization is OpenFGA's job, not this module's."""

from dataclasses import dataclass
from typing import Any

import jwt

PLATFORM_ADMIN = "platform_admin"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    sub: str
    email: str | None
    name: str | None
    org_id: str | None  # Keycloak organization id (when the mapper emits it)
    org_alias: str | None  # == tenants.slug
    roles: frozenset[str]


class TokenVerifier:
    def __init__(self, jwks_url: str, issuer: str, audience: str, azp: str) -> None:
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600)
        self._issuer, self._audience, self._azp = issuer, audience, azp

    def verify(self, token: str) -> Principal:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as e:
            raise AuthError(str(e)) from e
        return principal_from_claims(claims, self._audience, self._azp)


def principal_from_claims(claims: dict[str, Any], audience: str, azp: str) -> Principal:
    if claims.get("azp") != azp:
        raise AuthError("unexpected azp")
    roles = frozenset(claims.get("resource_access", {}).get(audience, {}).get("roles", []))
    org_id, org_alias = _organization(claims.get("organization"))
    if org_alias is None and PLATFORM_ADMIN not in roles:
        raise AuthError("token has no organization")
    return Principal(
        sub=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name"),
        org_id=org_id,
        org_alias=org_alias,
        roles=roles,
    )


def _organization(claim: Any) -> tuple[str | None, str | None]:
    # Keycloak emits ["alias"] (organization scope) and/or {"alias": {"id": ...}} (nova-web mapper with
    # "add organization id"); with both, the claim is a list mixing the two. One org per user (ADR-018).
    if not claim:
        return None, None
    entries = claim if isinstance(claim, list) else [claim]
    ids: dict[str, str | None] = {}
    for e in entries:
        if isinstance(e, str):
            ids.setdefault(e, None)
        elif isinstance(e, dict):
            for alias, attrs in e.items():
                ids[str(alias)] = str(attrs["id"]) if isinstance(attrs, dict) and attrs.get("id") else None
        else:
            raise AuthError("malformed organization claim")
    if len(ids) != 1:
        raise AuthError("token must carry exactly one organization")
    alias, org_id = next(iter(ids.items()))
    return org_id, alias
