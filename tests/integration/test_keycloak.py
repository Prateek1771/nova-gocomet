"""Real Keycloak with the committed realm: tokens carry org + roles and pass nova_core validation."""

from collections.abc import Iterator

import httpx
import pytest
from testcontainers.community.keycloak import KeycloakContainer

from nova_core.auth import AuthError, Principal, TokenVerifier

from .conftest import ROOT


@pytest.fixture(scope="module")
def realm() -> Iterator[str]:
    kc = KeycloakContainer("quay.io/keycloak/keycloak:26.4").with_realm_import_file(
        str(ROOT / "infra/keycloak/realm-nova.json")
    )
    with kc:
        yield f"{kc.get_url().rstrip('/')}/realms/nova"


def _login(realm: str, user: str) -> str:
    r = httpx.post(
        f"{realm}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "nova-web",
            "client_secret": "nova-web-dev-secret",
            "username": user,
            "password": "dev",
            "scope": "openid organization",
        },
    )
    r.raise_for_status()
    token: str = r.json()["access_token"]
    return token


def _verify(realm: str, token: str) -> Principal:
    return TokenVerifier(f"{realm}/protocol/openid-connect/certs", realm, "nova-api", "nova-web").verify(
        token
    )


@pytest.mark.parametrize(
    ("user", "org", "role"),
    [
        ("ops@acme", "acme", "ops_exec"),
        ("fin@acme", "acme", "finance"),
        ("ops@bolt", "bolt", "ops_exec"),
        ("platform", None, "platform_admin"),
    ],
)
def test_token_principal(realm: str, user: str, org: str | None, role: str) -> None:
    p = _verify(realm, _login(realm, user))
    assert p.org_alias == org
    assert (p.org_id is not None) == (org is not None)
    assert p.roles == {role}


def test_wrong_audience_rejected(realm: str) -> None:
    token = _login(realm, "ops@acme")
    with pytest.raises(AuthError):
        TokenVerifier(f"{realm}/protocol/openid-connect/certs", realm, "other-api", "nova-web").verify(token)
